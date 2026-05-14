using ESOL.AlarmLib;
using ESOL.CommonLib;
using ESOL.LogLib;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace PartsFineStage
{
    public class KIMMCtrl : BaseFineStageCtrl
    {
        private bool _isWarning;
        private bool _isScanStop;
        private bool _isExitThread;
        private bool _isConnected;
        private bool _isServoOnState;
        private bool _isAutoFocusEnableState;

        private bool _returnStartEvent;
        private bool _returnStopEvent;
        private bool _returnServoOnEvent;
        private bool _returnServoOffEvent;
        private bool _returnMoveAckEvent;
        private bool _returnMoveDoneEvent;
        private bool _returnScanEvent;
        private bool _returnScanDoneEvent;
        private bool _returnGetAxisEvent;
        private bool _returnGetAllAxisEvent;
        private bool _returnHomingEvent;
        private bool _returnScanStopEvent;

        private double[] _plusLimitPos = new double[6];
        private double[] _minusLimitPos = new double[6];
        /// <summary>
        /// um, urad
        /// </summary>
        private double[] _currentPosition = new double[6];
        private double[] _targetPosition = new double[6];
        private double[] _settingVelocity = new double[6];

        private double _initialPosition_X_um = 0.0;
        private double _initialPosition_Y_um = 0.0;
        private double _initialPosition_Z_um = 0.0;

        private int _timeout1 = 1000;
        private int _timeout2 = 2000;
        private int _timeout10 = 10000;
        private int _timeout60 = 60000;

        private Thread _devState;

        CommStopWatch _sw = new CommStopWatch();
        private CommFunctionQueue _funcQueue = new CommFunctionQueue(50);

        public KIMMCtrl(InitData data)
        {
            _isWarning = false;
            _isScanStop = false;
            _isExitThread = false;
            _isServoOnState = false;
            _isAutoFocusEnableState = false;

            _minusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_X] = data.XMinusLimit;
            _plusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_X] = data.XPlusLimit;
            _minusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Y] = data.YMinusLimit;
            _plusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Y] = data.YPlusLimit;
            _minusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z] = data.ZMinusLimit;
            _plusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z] = data.ZPlusLimit;
            _minusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX] = data.TxMinusLimit;
            _plusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX] = data.TxPlusLimit;
            _minusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY] = data.TyMinusLimit;
            _plusLimitPos[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY] = data.TyPlusLimit;

            _initialPosition_X_um = 6.0;
            _initialPosition_Y_um = 6.0;
            _initialPosition_Z_um = -400;

            this.OnReceiveEvent += new EventReceive(ReceiveData);
            this.OnDisconnectEvent += new EventDisconnect(ClientDisconnect);

            _devState = new Thread(new ThreadStart(DoWork));
            _devState.IsBackground = true;
            _devState.Start();

        }

        ~KIMMCtrl()
        {
            _isExitThread = true;

            if (IsConnected())
                Disconnect();
        }
        public override void Run(string ip, int port)
        {
            int ret = ConnectController(ip, port);
            if (ret == 0)
            {
                _isConnected = true;
            }
        }

        public override void Stop()
        {
            DisconnectController();
            _funcQueue.Stop();
            _isConnected = false;
            LOG.AddLog(EmLogType.FineStage_DLL, "Stop");
        }

        public int ConnectController(string ip, int port)
        {
            if (ip.Equals(string.Empty) || port.Equals(string.Empty))
                return -999;

            DisconnectController();

            if (Connect(ip, port))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, "Connected to controller.");
                _isConnected = true;
                _isExitThread = false;
            }
            else
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to connect to controller.");
                return -999;
            }
            return 0;
        }

        public void DisconnectController()
        {
            ClientDisconnect();
        }

        public override bool IsConnectState()
        {
            return IsConnected();
        }

        ////이벤트 메시지 처리
        private void ReceiveData(byte[] buffer)
        {
            // 받은 데이터를 문자열로 변환
            string message = Encoding.UTF8.GetString(buffer).Trim();
            //Position Receive 제외
            if(message.Contains("Get") == false)
            {
                LOG.AddLog(EmLogType.FineStage_DLL, $"[Received] {message}");
            }

            // Message 분리
            string formattedMessage = message.Replace(")", ",");
            string[] tokens = formattedMessage.Split('(', ',');
            string command = tokens[0];
            List<string> parameters = tokens.Skip(1).Take(tokens.Length - 2).ToList();

            if (command == "Start")
            {
                if (message == "Start(ack)")
                {
                    _returnStartEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Start command Ack Received.");
                }
            }
            else if (command == "Stop")
            {
                if (message == "Stop(ack)")
                {
                    _returnStopEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Stop command Ack Received");
                }
            }
            else if (command == "Move")
            {
                if (message == "Move(ack)")
                {
                    _returnMoveAckEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Move command Ack Received");
                }
                else if (message.Contains("Move(Done)"))
                {
                    _returnMoveDoneEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Move command Done Received");
                }
            }
            else if (command == "ScanRun")
            {
                if (message == "ScanRun(ack)")
                {
                    _returnScanEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Scan command Ack Received");
                }
            }
            else if (command == "Scan")
            {
                if (message == "Scan(Done)")
                {
                    _returnScanDoneEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Scan command Done Received");
                }
            }
            //Scan / Move Stop
            else if (command == "ScanStop")
            {
                _returnScanStopEvent = true;
                LOG.AddLog(EmLogType.FineStage_DLL, "Scan Stop command Ack Received");
            }
            else if (command == "Homing")
            {
                if (message == "Homing(ack)")
                {
                    _returnHomingEvent = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Homing command Ack Received");
                }
            }
            else if (command == "Servo")
            {
                if (message == "Servo(On)")
                {
                    _returnServoOnEvent = true;
                    _isServoOnState = true;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Servo is ON.");
                }
                else if (message == "Servo(Off)")
                {
                    _returnServoOffEvent = true;
                    _isServoOnState = false;
                    LOG.AddLog(EmLogType.FineStage_DLL, "Servo is OFF.");
                }
            }
            /*
             * 
             * 7,0,
             * 
             * 
             * 
             */
            else if (command == "Get")
            {
                if (parameters.Count == 8)
                {
                    // 6개의 데이터를 받았을 경우, 각 축의 위치 데이터를 업데이트
                    _currentPosition[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_X] = double.Parse(parameters[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_X]);
                    _currentPosition[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Y] = double.Parse(parameters[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Y]);
                    _currentPosition[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z] = double.Parse(parameters[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z]);
                    _currentPosition[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX] = double.Parse(parameters[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX]);
                    _currentPosition[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY] = double.Parse(parameters[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY]);
                    _isServoOnState = parameters[6] == "1"; // Servo 상태
                    _isAutoFocusEnableState = parameters[7] == "1"; // AF Enable/Disable 상태
                }
                else if (parameters.Count > 0)
                {
                    // 단일 축의 데이터만 받은 경우
                    int axis = int.Parse(parameters[0]) - 1;
                    _currentPosition[axis] = double.Parse(parameters[1]);
                }
            }
            else if (command == "Error")
            {
                SendScanStopProtocol();
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error Occured");

                if (parameters.Count == 0)
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: No details provided.");
                    return;
                }

                string errorType = parameters[0]; // Error 유형: TCP, HW, Move, Scan 등
                string errorDetails = parameters.Count > 1 ? parameters[1] : string.Empty; // 상세 메시지

                if (errorType == "TCP")
                {
                    if (errorDetails == " Invalid Command")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "TCP Error: Invalid Command received.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_INVALID_COMMAND_ERROR);
                    }
                    else if (errorDetails == " Another task is already in progress")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "TCP Error: Another task is already in progress.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_ANOTHER_TASK_IS_PROGRESS);
                    }
                    else if (errorDetails == " Servo control is currently disabled")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "TCP Error: Servo control is currently disabled.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_IS_NOT_SERVO_ON_STATE);
                    }
                    else
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, $"Unknown TCP error: {errorDetails}");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_UNKNOWN_COMMAND_ERROR);
                    }
                }
                else if (errorType == "HW")
                {
                    if (errorDetails == " Encoder Fault")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Hardware Error: Encoder fault detected.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_ENCODER_FAULT_ERROR);
                    }
                    else if (errorDetails == " XY Stage Fault")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Hardware Error: XY stage fault detected.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_XY_STAGE_FAULT);
                    }
                    else if (errorDetails == " TipTilt Stage Motor Driver Fault")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Hardware Error: TipTilt stage motor driver fault detected.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_TIP_TILT_STAGE_DRIVER_FAULT);
                    }
                    else
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, $"Unknown Hardware error: {errorDetails}");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_UNKNOWN_HW_ERROR);
                    }
                }
                else if (errorType == "Move")
                {
                    if (errorDetails == " Target position out of range")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Move Error: Target position is out of range.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_MOVE_TARGET_POSITION_OUT_OF_RANGE_ERROR);
                    }
                    else
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, $"Unknown Move error: {errorDetails}");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_UNKNOWN_MOVE_ERROR);
                    }
                }
                else if (errorType == "Scan")
                {
                    if (errorDetails == " Invalid parameters")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Scan Error: Invalid parameters provided.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_SCAN_INVALID_PARAMETER_ERROR);
                    }
                    else if (errorDetails == " Profile generation failed")
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Scan Error: Profile generation failed.");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_PROFILE_GENERATION_FAILED_ERROR);
                    }
                    else
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, $"Unknown Scan error: {errorDetails}");
                        ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_UNKNOWN_SCAN_ERROR);
                    }
                }
                else
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, $"Unknown error type: {errorType}, Details: {errorDetails}");
                    //ALARM.Knd.Ctrl.SetAlarm(ALARM.ENUM_ALARMCODE.FINE_STAGE_UNKNOWN_SCAN_ERROR);
                }

            }
            else if (command == "Notification")
            {
                _isWarning = true;
            }
            else
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, $"Unhandled message: {message}");
            }
        }

        private bool SendStartProtocol()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Sending START command...");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return false;
            }
            _returnStartEvent = false;

            if (!Send("START()\r\n"))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send START command.");
                return false;
            }

            LOG.AddLog(EmLogType.FineStage_DLL, "Sending START Command Complete");
            return true;
        }

        private bool SendStopProtocol()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Sending STOP command...");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return false;
            }
            _returnStopEvent = false;

            if (!Send("STOP()\r\n"))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send STOP command.");
                return false;
            }

            LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Sending Stop command Complete");
            return true;
        }

        private bool SendAFOnProtocol(double range_um)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Sending AF On command...");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return false;
            }

            //25-07-25 kkt AF rage 추가로 인한 수정.
            string msg = string.Format("AF(1,{0:0.000})\r\n", range_um);
            if (!Send(msg))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send AF(1) command.");
                return false;
            }

            LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Sending AF On command Complete");
            return true;
        }

        private bool SendAFOffProtocol()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Sending AF Off command...");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return false;
            }

            if (!Send("AF(0,1)\r\n"))//AF Off 시 2번째인자는 크게 상관없음.
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send AF(0) command.");
                return false;
            }

            LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Sending AF Off command Complete");
            return true;
        }

        private bool SendMoveProtocol(int axis, bool isAbsolute, double position, double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Sending Move command... Axis: {axis}, Type: {(isAbsolute ? "Abs" : "Rel")}, Position: {position}, Velocity: {velocity}");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return false;
            }

            //* Tx Ty는 Move명령 시  position rad 단위. 주의
            if(axis == (int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY || axis == (int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX)
            {
                position = position / 1000;
            }

            //KIMM Stage 이상으로 인하여 X/Y 축 미사용 25-06-14
            if(axis == (int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_X || axis == (int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Y)
            {
                return true;
            }

            string moveType = isAbsolute ? "Abs" : "Rel";
            string command = $"Move({axis},{moveType},{position},{velocity})\r\n";

            _returnMoveAckEvent = false;
            _returnMoveDoneEvent = false;

            if (!Send(command))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send Move command.");
                return false;
            }

            LOG.AddLog(EmLogType.FineStage_DLL, "Seding Move command Complete");
            return true;
        }

        private bool SendScanProtocol(int fov, int scanGrid, bool isTrainMode, bool isNewTrain)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Sending Scan command... FoV: {fov}, ScanGrid: {scanGrid}, TrainMode: {isTrainMode}, NewTrain: {isNewTrain}");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return false;
            }

            string command = $"ScanRun(BI,{fov},{scanGrid},{(isTrainMode ? 1 : 0)},{(isNewTrain ? 1 : 0)})\r\n";
            //string command = $"ScanRun(BI,5,50,10,0,10,0,0)\r\n";
            LOG.AddLog(EmLogType.FineStage_DLL, "Command : " + command);
            _returnScanEvent = false;

            if (!Send(command))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send Scan command.");
                return false;
            }

            LOG.AddLog(EmLogType.FineStage_DLL, "Sending Scan command Complete.");
            return true;
        }

        private void SendGetAllAxisPositionProtocol()
        {
            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return;
            }

            _funcQueue.EnqueueFunction(new Action(() =>
            {
                string command = "Get(6)\r\n";
                if (!Send(command))
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send Get command.");
                }

            }), null, isHighPriority: false, stateType: "CurrentPos");
        }

        private void SendScanStopProtocol()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Sending ScanStop command...");

            if (!IsConnected())
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Not connected.");
                return;
            }

            string command = "ScanStop()\r\n";
            _returnScanStopEvent = false;
            if (!Send(command))
            {
                LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send ScanStop command.");
            }

            LOG.AddLog(EmLogType.FineStage_DLL, "Sending ScanStop command Complete.");
        }

        public override async Task<bool> CheckInposition()
        {
            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if(_returnMoveAckEvent == true && _returnMoveDoneEvent == true)
                {
                    return true;
                }
                else
                {
                    return false;
                }
            }), null, isHighPriority: true, mustWaitPrevious: false);

            return (bool)(await task);
        }

        private void DoWork()
        {
            while (_isExitThread == false)
            {
                if (_isConnected)
                {
                    SendGetAllAxisPositionProtocol();
                }
                Thread.Sleep(100);
            }
        }

        public override bool ServoOnAll()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Start ServoOn");
            _returnServoOnEvent = false;

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (!SendStartProtocol())
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send START command.");
                    return false;
                }

                return true;
            }), null, isHighPriority: true, mustWaitPrevious: false);

            LOG.AddLog(EmLogType.FineStage_DLL, "End Servo On");

            return true;
        }

        public override bool ServoOffAll()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Start ServoOff");
            _returnServoOffEvent = false;

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (!SendStopProtocol())
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to send STOP command.");
                    return false;
                }

                return true;
            }), null, isHighPriority: true, mustWaitPrevious: false);

            
            LOG.AddLog(EmLogType.FineStage_DLL, "Servo is now OFF.");
            return true;
        }

        public override bool Initialize()
        {
            LOG.AddLog(EmLogType.FineStage_DLL, "Start Initialize_Stage");

            try
            {
                if (!IsConnected())
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Stage not connected.");
                    return false; // SCAN_STAGE_NOT_CONNECTED
                }

                // 서보가 꺼져 있으면 켜기
                if (!_isServoOnState)
                {
                    if (!ServoOnAll())
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "Error: Failed to turn on Servo.");
                        return false; // SCAN_STAGE_SERVO_ON_FAIL
                    }
                }

                LOG.AddLog(EmLogType.FineStage_DLL, "Finish Initialize_Stage");
                return true; // 성공
            }
            catch (Exception ex)
            {
                LOG.AddLog(EmLogType.FineStage_DLL, $"Exception in Initialize_Stage: {ex.Message}");
                return false; // SCAN_STAGE_INITIALIZE_FAIL
            }
        }

        public override bool Reference()
        {
            return false;
        }

        public override bool StopAllAxis()
        {
            _isScanStop = true;
            SendScanStopProtocol();
            return true;
        }

        public bool SetVelocity(int axis, double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Start SetVelocity : Axis {axis}, Velocity {velocity}");

            _settingVelocity[axis] = velocity;

            LOG.AddLog(EmLogType.FineStage_DLL, "Finish SetVelocity");
            return true;
        }

        public override bool SetVelocityTxTy(double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Start SetVelocityTxTy: {velocity} urad/s");
            _settingVelocity[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX] = velocity;
            _settingVelocity[(int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY] = velocity;
            LOG.AddLog(EmLogType.FineStage_DLL, "Finish SetVelocityTxTy");
            return true;
        }

        public override bool SetVelocityZ(double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Start SetVelocityZ: {velocity} um/s");

            bool result = SetVelocity((int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z, velocity);

            LOG.AddLog(EmLogType.FineStage_DLL, "Finish SetVelocityZ");
            return result;
        }

        public override bool SetVelocityX(double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Start SetVelocityX: {velocity} um/s");

            bool result = SetVelocity((int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_X, velocity);

            LOG.AddLog(EmLogType.FineStage_DLL, "Finish SetVelocityX");
            return result;
        }

        public override bool SetVelocityY(double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Start SetVelocityY: {velocity} um/s");

            bool result = SetVelocity((int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Y, velocity);

            LOG.AddLog(EmLogType.FineStage_DLL, "Finish SetVelocityY");
            return result;
        }

        public bool SetZTxTyVelocity(double velocity)
        {
            LOG.AddLog(EmLogType.FineStage_DLL, $"Start SetZTxTyVelocity: {velocity} um/s");

            bool success = true;

            success &= SetVelocity((int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_Z, velocity);
            success &= SetVelocity((int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TX, velocity);
            success &= SetVelocity((int)ENUM_FINE_STAGE_AXIS_NUM.AXIS_TY, velocity);

            LOG.AddLog(EmLogType.FineStage_DLL, "Finish SetZTxTyVelocity");
            return success;
        }

        public override double GetInitialPosition(int axis)
        {
            switch (axis)
            {
                case 1: return _initialPosition_X_um;
                case 2: return _initialPosition_Y_um;
                case 3: return _initialPosition_Z_um;
                default:
                    LOG.AddLog(EmLogType.FineStage_DLL, $"Error: Invalid axis index {axis}");
                    return 0.0;
            }
        }

        public override double GetCurrentPosition(int axis)
        {
            if (axis >= 0 && axis < _currentPosition.Length)
            {
                return _currentPosition[axis];
            }
            else
            {
                LOG.AddLog(EmLogType.FineStage_DLL, $"Error: Invalid axis index {axis}");
                return 0.0;
            }
        }

        public override double[] GetCapSensorData()
        {
            return null;
        }

        public override int RunInitializeMacro()
        {
            return 0;
        }

        public override bool Is_ServoOn()
        {
            return _isServoOnState;
        }

        public override bool Is_AutoFocusEnable()
        {
            return _isAutoFocusEnableState;
        }

        private void ClientDisconnect()
        {
            if (IsConnected())
                Disconnect();
        }

        public override async Task<bool> MoveAbsoluteMulti(int[] axis, double[] pos)
        {
            string log = string.Format("MoveAbsolute Multi Start");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            //25-06-17 kkt Fine Stage X/Y 축 Issue로 미사용
            return true;

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (axis.Length != pos.Length)
                {
                    return false;
                }

                //KIMM Stage 다축 동시 제어 불가능. 한축 이동 후 Move Done 확인 반복하도록 구성
                for (int i = 0; i < pos.Length; i++)
                {
                    if(SendMoveProtocol(i, true, pos[i], _settingVelocity[i]) == false)
                    {
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "MoveAbsolute Mutil Fail, SendMoveProtocoal Fail");
                        return false;
                    }

                    _sw.SetTimeStart();
                    while(true)
                    {
                        if(_returnMoveAckEvent == true && _returnMoveDoneEvent == true)
                        {
                            log = string.Format("MoveAbsolute Complete : {0} Target : {1}", axis, pos);
                            LOG.AddLog(EmLogType.FineStage_DLL, log);
                            break;
                        }

                        if(_sw.GetTimeOverCheck(20000))
                        {
                            log = string.Format("MoveAbsolute Timeover Axis : {0} Target : {1}", axis, pos);
                            LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, log);
                            return false;
                        }
                    }
                }

                return true;

            }), null, isHighPriority: true, mustWaitPrevious: true);

            log = string.Format("MoveAbsolute Multi End");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            return (bool)(await task);
        }

        public override async Task<bool> MoveAbsolute(int axis, double pos)
        {
            string log = string.Format("MoveAbsolute Start");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (SendMoveProtocol(axis, true, pos, _settingVelocity[axis]) == false)
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "MoveAbsolute Mutil Fail, SendMoveProtocoal Fail");
                    return false;
                }

                _sw.SetTimeStart();

                while (true)
                {
                    if (_returnMoveAckEvent == true /*&& _returnMoveDoneEvent == true*/)  //Done Event는 Wait Inposition에서만 확인 하도록 변경
                    {
                        log = string.Format("MoveAbsolute Complete : {0} Target : {1}", axis, pos);
                        LOG.AddLog(EmLogType.FineStage_DLL, log);
                        break;
                    }

                    if (_sw.GetTimeOverCheck(1000))
                    {
                        log = string.Format("MoveAbsolute Ack Timeover Axis : {0} Target : {1}", axis, pos);
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, log);
                        return false;
                    }
                }

                return true;

            }), null, isHighPriority: true, mustWaitPrevious: true);

            log = string.Format("MoveAbsolute End");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            return (bool)(await task);
        }

        public override async Task<bool> MoveRelative(int axis, double pos)
        {
            string log = string.Format("MoveRelative Start");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (SendMoveProtocol(axis, false, pos, _settingVelocity[axis]) == false)
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "MoveRelative Mutil Fail, SendMoveProtocoal Fail");
                    return false;
                }

                _sw.SetTimeStart();
                while (true)
                {
                    if (_returnMoveAckEvent == true && _returnMoveDoneEvent == true)
                    {
                        log = string.Format("MoveRelative Complete : {0} Target : {1}", axis, pos);
                        LOG.AddLog(EmLogType.FineStage_DLL, log);
                        break;
                    }

                    if (_sw.GetTimeOverCheck(10000))
                    {
                        log = string.Format("MoveRelative Timeover Axis : {0} Target : {1}", axis, pos);
                        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, log);
                        return false;
                    }
                }

                return true;

            }), null, isHighPriority: true, mustWaitPrevious: true);

            log = string.Format("MoveRelative End");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            return (bool)(await task);
        }

        public override Task<bool> RunScanning(int fov_nm, int grid_nm, int scanNum, bool isActiveDDL, int driftMargin_nm, int acc_nm, int dcc_nm, double frequency)
        {
            return base.RunScanning(fov_nm, grid_nm, scanNum, isActiveDDL, driftMargin_nm, acc_nm, dcc_nm, frequency);
        }

        public override Task<bool> RunLearning(int fov_nm, int grid_nm, int scanNum, int driftMargin_nm, int acc_nm, int dcc_nm, double frequency)
        {
            return base.RunLearning(fov_nm, grid_nm, scanNum, driftMargin_nm, acc_nm, dcc_nm, frequency);
        }

        public override async Task<bool> AutoFocusEnable(double range_um = 3)
        {
            string log = string.Format("AutoFocus Enable Start");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (!SendAFOnProtocol(range_um))
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "AutoFocusEnable Fail, SendAFOnProtocol Fail");
                    return false;
                }

                //_sw.SetTimeStart();

                //while (true)
                //{
                //    if (_returnMoveAckEvent == true && _returnMoveDoneEvent == true)
                //    {
                //        log = string.Format("MoveAbsolute Complete : {0} Target : {1}", axis, pos);
                //        LOG.AddLog(EmLogType.FineStage_DLL, log);
                //        break;
                //    }

                //    if (_sw.GetTimeOverCheck(20000))
                //    {
                //        log = string.Format("MoveAbsolute Timeover Axis : {0} Target : {1}", axis, pos);
                //        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, log);
                //        return false;
                //    }
                //}

                return true;

            }), null, isHighPriority: true, mustWaitPrevious: true);

            log = string.Format("AutoFocusEnable End");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            return (bool)(await task);
        }

        public override async Task<bool> AutoFocusDisable()
        {
            string log = string.Format("AutoFocus Disable Start");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            var task = _funcQueue.EnqueueFunction(new Func<bool>(() =>
            {
                if (SendAFOffProtocol())
                {
                    LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, "AutoFocusDisable Fail, SendAFOnProtocol Fail");
                    return false;
                }

                //_sw.SetTimeStart();

                //while (true)
                //{
                //    if (_returnMoveAckEvent == true && _returnMoveDoneEvent == true)
                //    {
                //        log = string.Format("MoveAbsolute Complete : {0} Target : {1}", axis, pos);
                //        LOG.AddLog(EmLogType.FineStage_DLL, log);
                //        break;
                //    }

                //    if (_sw.GetTimeOverCheck(20000))
                //    {
                //        log = string.Format("MoveAbsolute Timeover Axis : {0} Target : {1}", axis, pos);
                //        LOG.AddLog(EmLogType.FineStage_DLL, EmLogLevel.Error, log);
                //        return false;
                //    }
                //}

                return true;

            }), null, isHighPriority: true, mustWaitPrevious: true);

            log = string.Format("AutoFocusDisable End");
            LOG.AddLog(EmLogType.FineStage_DLL, log);

            return (bool)(await task);
        }
    }
}

