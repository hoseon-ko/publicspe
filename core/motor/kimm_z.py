"""
core/motor/kimm_z.py
KIMM Fine Stage — Z축 TCP 소켓 통신 (위치 조회 전용 우선 구현)

프로토콜 (KIMMCtrl.cs 기반):
  위치 요청 : Get(6)\r\n
  위치 응답 : Get(x,y,z,tx,ty,_,servo,af,)
              index 0=X, 1=Y, 2=Z, 3=Tx, 4=Ty

이동 명령은 connect/위치 확인 후 별도 활성화 예정.
"""

from __future__ import annotations

import socket
import logging
import threading
from typing import Optional

from core.logger import dev_logger

log = logging.getLogger(__name__)

AXIS_Z = 3  # ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z (기본 Z축 매칭)
AXIS_TX = 4
AXIS_TY = 5

class KIMMZController:
    """
    KIMM Fine Stage Z축 컨트롤러.

    사용 순서:
        ctrl = KIMMZController("192.168.1.100", 5000)
        ok   = ctrl.connect()
        z    = ctrl.current_z          # 폴링으로 최신값
        ctrl.disconnect()
    """

    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port

        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._send_lock = threading.Lock()  # 통신 전송용 락
        self._cmd_lock = threading.Lock()   # 모션 중복 방지용 락

        # 마지막으로 수신된 각 축 위치
        self._positions: list[float] = [0.0] * 6
        self._servo_on: bool = False
        self._is_moving: bool = False # V2 비차단용 플래그

        self._recv_buf = ""
        self._recv_thread: Optional[threading.Thread] = None

        # 이동 관련 이벤트
        self._ack_received = threading.Event()
        self._done_received = threading.Event()

        # 설정값 (UI에서 업데이트 예정)
        self.z_safety_limit = 10000.0  # um (상한 기본값)
        self.z_lower_limit = -10000.0  # um (하한 리밋 추가)
        self.default_velocity = 10.0  # um/s
        self.dry_run = False

    # ── 속성 ────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def current_z(self) -> float:
        """마지막으로 수신된 Z 위치 (um)."""
        return self._positions[AXIS_Z - 1]

    @property
    def is_moving(self) -> bool:
        return self._is_moving

    @property
    def servo_on(self) -> bool:
        return self._servo_on

    # ── 연결 / 해제 ─────────────────────────────────────────────────

    def connect(self) -> None:
        """TCP 연결. 실패 시 예외 발생."""
        if self._connected:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self.ip, self.port))
            sock.settimeout(None)           # 이후 recv는 블로킹
            self._sock = sock
            self._connected = True
            self._recv_buf = ""

            self._recv_thread = threading.Thread(
                target=self._recv_loop, daemon=True, name="kimm_recv"
            )
            self._recv_thread.start()
            dev_logger.info(f"KIMM Connected: {self.ip}:{self.port}")
        except Exception as e:
            self._connected = False
            dev_logger.error(f"KIMM Connection Failed: {e}")
            raise ConnectionError(f"KIMM 연결 실패: {e}")

    def disconnect(self):
        """연결 종료."""
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        log.info("[KIMM] Disconnected")

    # ── 위치 요청 ────────────────────────────────────────────────────

    def request_position(self):
        """Get(6) 명령 전송 — 응답은 수신 루프에서 _positions 갱신."""
        self._send("Get(6)\r\n")

    # ── 이동 명령 ────────────────────────────────────────────────────

    def move_to_z(self, target_um: float, velocity: Optional[float] = None,
                  done_timeout_s: Optional[float] = None) -> None:
        """Z축 절대 이동. done_timeout_s 지정 시 Done 대기 타임아웃을 덮어쓴다."""
        if not self._connected and not self.dry_run:
            raise ConnectionError("KIMM Not connected")

        vel = velocity if velocity is not None else self.default_velocity

        # 안전 리밋 체크 (상한/하한 동시 체크)
        if target_um > self.z_safety_limit or target_um < self.z_lower_limit:
            raise ValueError(f"Target Z({target_um:.1f}) out of bounds [{self.z_lower_limit:.1f}, {self.z_safety_limit:.1f}]")

        if not self._cmd_lock.acquire(blocking=False):
            raise RuntimeError("Motion already in progress. Ignoring command.")

        try:
            # AXIS_Z가 4(Tx) 또는 5(Ty) 이면 패킷 전송 시 position / 1000 처리 (C# KIMMCtrl.cs 규격)
            target_val = target_um
            if AXIS_Z in (4, 5):
                target_val = target_um / 1000.0

            cmd = f"Move({AXIS_Z},Abs,{target_val:.6f},{vel:.0f})\r\n"

            if self.dry_run:
                log.info(f"[KIMM DRY-RUN] {cmd.strip()} (Limit={self.z_safety_limit})")
                return

            self._ack_received.clear()
            self._done_received.clear()

            self._send(cmd)
            # Ack 대기 (5초)
            if not self._ack_received.wait(timeout=5.0):
                raise TimeoutError("Move Ack timeout")
            # Done 대기 (기본 30초, 호출자가 override 가능)
            done_to = 30.0 if done_timeout_s is None else float(done_timeout_s)
            if not self._done_received.wait(timeout=done_to):
                raise TimeoutError(f"Move Done timeout after {done_to:.1f}s")

            log.info(f"[KIMM] Move_to {target_um:.2f} 완료")
        finally:
            self._cmd_lock.release()

    def move_to_z_async(self, target_um: float, velocity: Optional[float] = None) -> None:
        """Z축 절대 이동 (비차단)."""
        if not self._connected and not self.dry_run:
            raise ConnectionError("KIMM Not connected")
        vel = velocity if velocity is not None else self.default_velocity
        if target_um > self.z_safety_limit or target_um < self.z_lower_limit:
            raise ValueError(f"Limit Violation")

        self._is_moving = True
        self._ack_received.clear()
        self._done_received.clear()
        
        target_val = target_um
        if AXIS_Z in (4, 5):
            target_val = target_um / 1000.0
            
        self._send(f"Move({AXIS_Z},Abs,{target_val:.6f},{vel:.0f})\r\n")

    def move_by_z(self, delta_um: float, velocity: Optional[float] = None) -> None:
        """Z축 상대 이동."""
        if not self._connected and not self.dry_run:
            raise ConnectionError("KIMM Not connected")
            
        vel = velocity if velocity is not None else self.default_velocity
        target_um = self.current_z + delta_um
        
        if target_um > self.z_safety_limit or target_um < self.z_lower_limit:
            raise ValueError(f"Target Z({target_um:.1f}) out of bounds [{self.z_lower_limit:.1f}, {self.z_safety_limit:.1f}]")

        if not self._cmd_lock.acquire(blocking=False):
            raise RuntimeError("Motion already in progress. Ignoring command.")

        try:
            delta_val = delta_um
            if AXIS_Z in (4, 5):
                delta_val = delta_um / 1000.0

            cmd = f"Move({AXIS_Z},Rel,{delta_val:.6f},{vel:.0f})\r\n"

            if self.dry_run:
                log.info(f"[KIMM DRY-RUN] {cmd.strip()} (Limit={self.z_safety_limit})")
                return

            self._ack_received.clear()
            self._done_received.clear()

            self._send(cmd)
            if not self._ack_received.wait(timeout=5.0):
                raise TimeoutError("Move Ack timeout")
            if not self._done_received.wait(timeout=30.0):
                raise TimeoutError("Move Done timeout")
                
            log.info(f"[KIMM] Move_by {delta_um:+.2f} 완료")
        finally:
            self._cmd_lock.release()

    # ── 내부: 전송 ────────────────────────────────────────────────────

    def _send(self, command: str) -> None:
        with self._send_lock:
            if not self._connected or self._sock is None:
                raise ConnectionError("Socket not connected")
            try:
                self._sock.sendall(command.encode("utf-8"))
            except Exception as e:
                self._connected = False
                raise ConnectionError(f"Send error: {e}")

    # ── 내부: 수신 루프 ───────────────────────────────────────────────

    def _recv_loop(self):
        """select로 데이터가 있을 때만 recv (C# 비동기 느낌)"""
        import select
        try:
            while self._connected and self._sock:
                rlist, _, _ = select.select([self._sock], [], [], 1.0)  # 1초 타임아웃
                if rlist:
                    try:
                        data = self._sock.recv(4096)
                    except OSError:
                        break
                    if not data:
                        log.warning("[KIMM] Server closed connection")
                        break
                    self._recv_buf += data.decode("utf-8", errors="replace")
                    self._parse_buffer()
                # else: 타임아웃(데이터 없음) → 그냥 루프 반복
        finally:
            self._connected = False
            log.info("[KIMM] Recv loop exited")

    def _parse_buffer(self):
        """버퍼에서 '\r\n' 또는 '\n' 단위로 메시지 분리 후 처리."""
        while True:
            for sep in ("\r\n", "\n"):
                if sep in self._recv_buf:
                    line, self._recv_buf = self._recv_buf.split(sep, 1)
                    line = line.strip()
                    if line:
                        self._handle(line)
                    break
            else:
                break

    def _handle(self, msg: str):
        # 위치 응답은 디버그 레벨 (빈번해서 로그 노이즈 방지)
        if msg.startswith("Get"):
            self._parse_get(msg)
        elif msg == "Move(ack)":
            self._ack_received.set()
        elif msg == "Move(Done)":
            self._is_moving = False
            self._done_received.set()
        elif msg.startswith("Error"):
            log.error(f"[KIMM] {msg}")
        elif msg.startswith("Notification"):
            log.warning("[KIMM] Warning notification received")
        else:
            log.info(f"[KIMM RX] {msg}")

    def _parse_get(self, msg: str):
        """
        Get(x,y,z,tx,ty,_,servo,af,)  →  _positions 갱신.
        KIMMCtrl.cs ReceiveData의 Get 파싱 로직과 동일.
        """
        try:
            # ')' → ',' 치환 후 '(' 기준 분리
            params_str = msg.replace(")", ",").split("(", 1)
            if len(params_str) < 2:
                return
            params = [p.strip() for p in params_str[1].split(",") if p.strip()]
            if len(params) >= 8:
                for i in range(6):      # X Y Z Tx Ty Tz
                    self._positions[i] = float(params[i])
                self._servo_on = params[6] == "1"
            elif len(params) >= 2:
                # 단일 축 응답
                axis = int(params[0]) - 1
                if 0 <= axis < 6:
                    self._positions[axis] = float(params[1])
        except Exception as e:
            log.debug(f"[KIMM] Get parse error ({msg}): {e}")
