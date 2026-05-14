"""
KIMM Fine Stage - Z축 제어 (TCP 소켓)
KIMMCtrl.cs 프로토콜 기반

프로토콜:
  송신: Move(2,Abs,{pos},{vel})\r\n  또는  Move(2,Rel,{pos},{vel})\r\n
  수신: Move(ack)  →  Move(Done)
  위치 조회: Get(6)\r\n  →  Get(x,y,z,tx,ty,_,servo,af,)
"""

import socket
import threading
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("KIMM_Z")

AXIS_Z = 3  # ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z


class KIMMZController:
    def __init__(self, ip: str, port: int, z_safety_limit: float = 0.0):
        """
        ip             : 컨트롤러 IP
        port           : 컨트롤러 포트
        z_safety_limit : Z 안전 리밋 (um). 현재 Z >= 이 값이면 이동 차단.
                         기본값 0.0 → Z가 0 이상이면 차단
        """
        self.ip = ip
        self.port = port
        self.z_safety_limit = z_safety_limit

        self._sock: socket.socket | None = None
        self._connected = False
        self._current_z: float = 0.0

        self._ack_received = threading.Event()
        self._done_received = threading.Event()

        self._recv_thread: threading.Thread | None = None
        self._recv_buf = ""

    # ──────────────────────────────────────────
    # 연결 / 해제
    # ──────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0)
            self._sock.connect((self.ip, self.port))
            self._connected = True
            log.info(f"Connected to {self.ip}:{self.port}")

            self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self._recv_thread.start()
            return True
        except Exception as e:
            log.error(f"Connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self):
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        log.info("Disconnected")

    # ──────────────────────────────────────────
    # 수신 루프 (백그라운드)
    # ──────────────────────────────────────────

    def _recv_loop(self):
        while self._connected:
            try:
                data = self._sock.recv(1024)
                if not data:
                    log.warning("Connection closed by server")
                    self._connected = False
                    break
                self._recv_buf += data.decode("utf-8", errors="replace")
                self._process_buffer()
            except socket.timeout:
                continue
            except Exception as e:
                if self._connected:
                    log.error(f"Recv error: {e}")
                break

    def _process_buffer(self):
        while "\r\n" in self._recv_buf or "\n" in self._recv_buf:
            sep = "\r\n" if "\r\n" in self._recv_buf else "\n"
            line, self._recv_buf = self._recv_buf.split(sep, 1)
            line = line.strip()
            if line:
                self._handle_message(line)

    def _handle_message(self, message: str):
        # 위치 응답은 로그 생략
        if not message.startswith("Get"):
            log.info(f"[RX] {message}")

        if message == "Move(ack)":
            self._ack_received.set()
        elif message == "Move(Done)":
            self._done_received.set()
        elif message.startswith("Get"):
            self._parse_get(message)
        elif message.startswith("Error"):
            log.error(f"Controller Error: {message}")

    def _parse_get(self, message: str):
        # Get(x,y,z,tx,ty,_,servo,af,)  ← ')'를 ','로 치환 후 분리
        try:
            fmt = message.replace(")", ",")
            tokens = fmt.split("(", 1)
            if len(tokens) < 2:
                return
            params = [p.strip() for p in tokens[1].split(",") if p.strip()]
            if len(params) >= 3:
                self._current_z = float(params[AXIS_Z])
        except Exception:
            pass

    # ──────────────────────────────────────────
    # 전송
    # ──────────────────────────────────────────

    def _send(self, command: str) -> bool:
        try:
            self._sock.sendall(command.encode("utf-8"))
            log.info(f"[TX] {command.strip()}")
            return True
        except Exception as e:
            log.error(f"Send failed: {e}")
            return False

    # ──────────────────────────────────────────
    # 위치 조회
    # ──────────────────────────────────────────

    def get_z_position(self, wait_sec: float = 0.3) -> float:
        """최신 Z 위치를 요청하고 반환 (um)"""
        self._send("Get(6)\r\n")
        time.sleep(wait_sec)
        return self._current_z

    # ──────────────────────────────────────────
    # Z축 이동 (핵심 안전 로직 포함)
    # ──────────────────────────────────────────

    def move_z_absolute(self, target_um: float, velocity: float = 10.0,
                        timeout_sec: float = 30.0) -> bool:
        """
        Z축 절대 이동.
        현재 Z 포지션 >= z_safety_limit 이면 이동 차단.

        target_um  : 목표 위치 (um)
        velocity   : 이동 속도 (um/s)
        """
        if not self._connected:
            log.error("Not connected")
            return False

        current_z = self.get_z_position()
        log.info(f"Current Z: {current_z:.3f} um  |  Safety Limit: {self.z_safety_limit:.3f} um")

        if current_z >= self.z_safety_limit:
            log.error(
                f"[SAFETY BLOCK] Z 이동 차단: 현재 Z({current_z:.3f} um) >= 리밋({self.z_safety_limit:.3f} um)"
            )
            return False

        cmd = f"Move({AXIS_Z},Abs,{target_um},{velocity})\r\n"
        self._ack_received.clear()
        self._done_received.clear()

        if not self._send(cmd):
            return False

        # Ack 대기
        if not self._ack_received.wait(timeout=5.0):
            log.error("Move Ack timeout")
            return False

        # Done 대기
        if not self._done_received.wait(timeout=timeout_sec):
            log.error("Move Done timeout")
            return False

        log.info(f"Z Move Done → {target_um} um")
        return True

    def move_z_relative(self, delta_um: float, velocity: float = 10.0,
                        timeout_sec: float = 30.0) -> bool:
        """
        Z축 상대 이동.
        현재 Z 포지션 >= z_safety_limit 이면 이동 차단.

        delta_um  : 이동량 (um, 양수=+방향, 음수=-방향)
        velocity  : 이동 속도 (um/s)
        """
        if not self._connected:
            log.error("Not connected")
            return False

        current_z = self.get_z_position()
        log.info(f"Current Z: {current_z:.3f} um  |  Safety Limit: {self.z_safety_limit:.3f} um")

        if current_z >= self.z_safety_limit:
            log.error(
                f"[SAFETY BLOCK] Z 이동 차단: 현재 Z({current_z:.3f} um) >= 리밋({self.z_safety_limit:.3f} um)"
            )
            return False

        cmd = f"Move({AXIS_Z},Rel,{delta_um},{velocity})\r\n"
        self._ack_received.clear()
        self._done_received.clear()

        if not self._send(cmd):
            return False

        if not self._ack_received.wait(timeout=5.0):
            log.error("Move Ack timeout")
            return False

        if not self._done_received.wait(timeout=timeout_sec):
            log.error("Move Done timeout")
            return False

        log.info(f"Z Relative Move Done (+{delta_um} um)")
        return True


# ──────────────────────────────────────────────
# 사용 예시
# ──────────────────────────────────────────────

if __name__ == "__main__":
    ctrl = KIMMZController(
        ip="192.168.1.100",
        port=5000,
        z_safety_limit=0.0,   # Z >= 0 um 이면 이동 차단
    )

    if not ctrl.connect():
        exit(1)

    try:
        # 현재 Z 위치 확인
        z = ctrl.get_z_position()
        print(f"현재 Z: {z:.3f} um")

        # 절대 이동: -100 um 으로
        ctrl.move_z_absolute(target_um=-100.0, velocity=10.0)

        # 상대 이동: -50 um 더
        ctrl.move_z_relative(delta_um=-50.0, velocity=5.0)

    finally:
        ctrl.disconnect()
