import os
import json
import base64
from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QTimer, QByteArray, QBuffer
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from core.logger import dev_logger

class EuvLaserController(QObject):
    # Signals for UI synchronization
    status_updated = pyqtSignal(dict)           # Emits current states dictionary
    error_occurred = pyqtSignal(str, str)       # Emits (req_type, error_msg)
    login_status_changed = pyqtSignal(bool, str)# Emits (success, message_or_token)
    alarm_triggered = pyqtSignal(float, float)  # Emits (current_temp, min_temp)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.manager.finished.connect(self._on_http_response)
        
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(3000)
        self.poll_timer.timeout.connect(self.poll_status)

        # Internals
        self.states = {
            "temp": "N/A",
            "power": "N/A",
            "duty": "N/A",
            "pulse": "N/A",
            "hf": "N/A"
        }
        self.ip = "127.0.0.1"
        self.port = "5643"
        self.session_token = ""
        self.auth_type_idx = 0  # 0: ID/PW (Basic), 1: Bearer Token
        
        self.login_success_callback = None
        self.is_logging_in = False
        
        # Temp alarm configuration
        self.alarm_enabled = False
        self.alarm_min_temp = 200.0

    def set_connection_info(self, ip: str, port: str):
        self.ip = ip or "127.0.0.1"
        self.port = port or "5643"

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.port}"

    def set_auth_config(self, auth_type_idx: int):
        self.auth_type_idx = auth_type_idx
        self.session_token = ""  # Reset token cache on auth method change

    def set_alarm_config(self, enabled: bool, min_temp: float):
        self.alarm_enabled = enabled
        self.alarm_min_temp = min_temp

    def start_polling(self, interval_ms: int = 3000):
        self.poll_timer.setInterval(interval_ms)
        self.poll_timer.start()
        self.poll_status()  # Immediate poll

    def stop_polling(self):
        self.poll_timer.stop()
        self.states = {"temp": "N/A", "power": "N/A", "duty": "N/A", "pulse": "N/A", "hf": "N/A"}
        self.session_token = ""
        self.status_updated.emit(self.states)

    def is_polling(self) -> bool:
        return self.poll_timer.isActive()

    def get_auth_headers(self) -> dict:
        headers = {}
        if self.session_token:
            if self.session_token.startswith("Basic "):
                headers[b"Authorization"] = self.session_token.encode("utf-8")
            else:
                headers[b"Authorization"] = f"Bearer {self.session_token}".encode("utf-8")
                headers[b"X-Auth-Token"] = self.session_token.encode("utf-8")
        return headers

    def _get_laser_token_from_file(self, filepath: str) -> str:
        """JSON 파일에서 토큰 값 파싱 및 추출, 또는 Plain Text 토큰 반환."""
        if not filepath or not os.path.exists(filepath) or not os.path.isfile(filepath):
            return filepath

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if isinstance(data, dict):
                for candidate in ["token", "access_token", "apiKey", "api_key", "key", "access-token", "id_token"]:
                    for k, v in data.items():
                        if k.lower() == candidate.lower():
                            return str(v).strip()
                for k, v in data.items():
                    if isinstance(v, (str, int, float)):
                        return str(v).strip()
            elif isinstance(data, str):
                return data.strip()
        except Exception as e:
            dev_logger.warning(f"[DeepAlign] Failed to parse laser token JSON file {filepath}: {e}")
        return filepath

    def login_to_laser_server(self, success_callback, username: str = "", password: str = "", token_input: str = ""):
        self.login_success_callback = success_callback

        # 1. Bearer Token Mode
        if self.auth_type_idx == 1:
            self.session_token = self._get_laser_token_from_file(token_input)
            self.login_status_changed.emit(True, "Logged in (Bearer Token)")
            if self.login_success_callback:
                self.login_success_callback()
            return

        # 2. ID/PW Mode (Basic Auth)
        credentials = f"{username}:{password}"
        credentials_bytes = credentials.encode("utf-8")
        base64_credentials = base64.b64encode(credentials_bytes).decode("utf-8")
        self.session_token = f"Basic {base64_credentials}"
        
        # In Basic mode, we verify the credentials by calling access level check
        self.login_status_changed.emit(True, "Logged in (Basic Auth)")
        if self.login_success_callback:
            self.login_success_callback()

    def poll_status(self) -> None:
        if not self.session_token:
            # Trigger login internally if we don't have token but have connection setup.
            # Usually tab handles login before calling poll_status, but as a fallback:
            return

        endpoints = {
            "GET_TEMP": f"{self.base_url}/api/euvChamber/target/disk/temperature/value",
            "GET_POWER": f"{self.base_url}/api/euvChamber/euvPower/value",
            "GET_DUTY": f"{self.base_url}/api/euvChamber/euvDutyCycle/value",
            "GET_PULSE": f"{self.base_url}/api/laser/enablePulse",
            "GET_HF": f"{self.base_url}/api/laser/enableHighFrequency"
        }

        for req_type, url_str in endpoints.items():
            request = QNetworkRequest(QUrl(url_str))
            for k, v in self.get_auth_headers().items():
                request.setRawHeader(k, v)

            reply = self.manager.get(request)
            reply.setProperty("req_type", req_type)

    def control_pulse(self, enabled: bool):
        url_str = f"{self.base_url}/api/laser/enablePulse"
        self._send_put_request("PUT_PULSE", url_str, enabled)

    def control_hf(self, enabled: bool):
        url_str = f"{self.base_url}/api/laser/enableHighFrequency"
        self._send_put_request("PUT_HF", url_str, enabled)

    def _send_put_request(self, req_type: str, url_str: str, enabled: bool):
        request = QNetworkRequest(QUrl(url_str))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        for k, v in self.get_auth_headers().items():
            request.setRawHeader(k, v)

        # QByteArray → QBuffer(QIODevice) 로 래핑해야 PyQt6에서 바디가 확실히 전송됨
        body_bytes = b"true" if enabled else b"false"
        buf = QBuffer()
        buf.setData(QByteArray(body_bytes))
        buf.open(QBuffer.OpenModeFlag.ReadOnly)

        dev_logger.info(f"[DeepAlign] PUT {url_str} body={body_bytes}")

        reply = self.manager.sendCustomRequest(request, b"PUT", buf)
        buf.setParent(reply)  # reply 살아있는 동안 buf 유지
        reply.setProperty("req_type", req_type)
        reply.setProperty("target_status", "on" if enabled else "off")

    def query_user_access_level(self, username: str):
        if not self.session_token:
            return

        url_str = f"{self.base_url}/api/server/users/{username}/accessLevel"
        request = QNetworkRequest(QUrl(url_str))
        for k, v in self.get_auth_headers().items():
            request.setRawHeader(k, v)

        reply = self.manager.get(request)
        reply.setProperty("req_type", "GET_ACCESS_LEVEL")
        reply.setProperty("user", username)
        dev_logger.info(f"[DeepAlign] Querying access level for user '{username}' -> {url_str}")

    def _on_http_response(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        req_type = reply.property("req_type")
        if not req_type:
            return

        # 1. Error Handling
        if reply.error() != QNetworkReply.NetworkError.NoError:
            status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            err_msg = reply.errorString()
            err_body = reply.readAll().data().decode("utf-8", errors="replace").strip()

            # Print HTTP error logs (서버 응답 바디까지 포함)
            dev_logger.warning(
                f"[DeepAlign] HTTP Error: Req={req_type}, Status={status_code}, "
                f"Msg={err_msg}, ServerBody={err_body[:300]}"
            )
            
            if status_code in (401, 403):
                dev_logger.warning(f"[DeepAlign] Unauthorized ({status_code}). Clearing cached session token.")
                self.session_token = ""

            # Update state errors
            if req_type == "GET_TEMP":
                self.states["temp"] = "Error"
            elif req_type == "GET_POWER":
                self.states["power"] = "Error"
            elif req_type == "GET_DUTY":
                self.states["duty"] = "Error"
            elif req_type == "GET_PULSE":
                self.states["pulse"] = "Error"
            elif req_type == "GET_HF":
                self.states["hf"] = "Error"

            self.error_occurred.emit(req_type, err_msg)
            self.status_updated.emit(self.states)
            return

        # 2. Success Handling
        data_bytes = reply.readAll().data()
        data_str = data_bytes.decode("utf-8").strip()
        dev_logger.info(f"[DeepAlign] HTTP Success: Req={req_type}, Response={data_str[:200]}")

        if req_type == "GET_TEMP":
            try:
                parsed = json.loads(data_str)
                val = float(parsed["value"]) if (isinstance(parsed, dict) and "value" in parsed) else float(data_str)
                self.states["temp"] = f"{val:.1f} °C"

                # Alarm checking
                if self.alarm_enabled and val <= self.alarm_min_temp:
                    self.alarm_triggered.emit(val, self.alarm_min_temp)
            except Exception:
                self.states["temp"] = data_str[:15]

        elif req_type == "GET_POWER":
            try:
                parsed = json.loads(data_str)
                val = float(parsed["value"]) if (isinstance(parsed, dict) and "value" in parsed) else float(data_str)
                self.states["power"] = self._format_euv_power(val)
            except Exception:
                self.states["power"] = data_str[:15]

        elif req_type == "GET_DUTY":
            try:
                parsed = json.loads(data_str)
                val = float(parsed["value"]) if (isinstance(parsed, dict) and "value" in parsed) else float(data_str)
                self.states["duty"] = f"{val:.1f} %"
            except Exception:
                self.states["duty"] = data_str[:15]

        elif req_type == "GET_PULSE":
            is_pulse = ("true" in data_str.lower() or "on" in data_str.lower() or "1" == data_str or "enable" in data_str.lower())
            self.states["pulse"] = "ON" if is_pulse else "OFF"

        elif req_type == "GET_HF":
            is_hf = ("true" in data_str.lower() or "on" in data_str.lower() or "1" == data_str or "enable" in data_str.lower())
            self.states["hf"] = "ON" if is_hf else "OFF"

        elif req_type == "PUT_PULSE":
            target_status = reply.property("target_status")
            self.states["pulse"] = "ON" if (target_status == "on") else "OFF"

        elif req_type == "PUT_HF":
            target_status = reply.property("target_status")
            self.states["hf"] = "ON" if (target_status == "on") else "OFF"

        elif req_type == "GET_ACCESS_LEVEL":
            user = reply.property("user") or "unknown"
            try:
                parsed = json.loads(data_str)
                access_level = str(parsed.get("value", data_str)).strip()
            except Exception:
                access_level = data_str.strip()
            dev_logger.info(f"[DeepAlign] User Access Level: User={user}, Level={access_level}")
            self.login_status_changed.emit(True, f"Logged in as {user} ({access_level})")

        self.status_updated.emit(self.states)

    def _format_euv_power(self, val_watts: float) -> str:
        """EUV Power (W) 값을 크기에 따라 W, mW, uW 단위로 자동 변환."""
        if val_watts >= 1.0:
            return f"{val_watts:.3f} W"
        elif val_watts >= 0.001:
            return f"{val_watts * 1000.0:.1f} mW"
        else:
            return f"{val_watts * 1000000.0:.1f} \u03bcW"  # μW
