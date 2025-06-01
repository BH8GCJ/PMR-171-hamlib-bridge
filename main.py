# 本程序遵循 GNU 通用公共许可证 v3.0 (GPLv3)
# 详情请参阅 LICENSE 文件或访问 https://www.gnu.org/licenses/gpl-3.0.html
# 版权所有 (C) 2025 BH8GCJ

import socket
import serial
import struct
import threading
import crcmod
import serial.tools.list_ports
import sys
import signal

class PMR171Bridge:
    MODE_MAP = {
        0: 'USB',
        1: 'LSB',
        2: 'CWR',
        3: 'CWL',
        4: 'AM',
        5: 'WFM',
        6: 'NFM',
        7: 'DIGI',
        8: 'PKT'
    }

    def __init__(self, serial_port, baudrate=115200):
        self.ser = serial.Serial(serial_port, baudrate=baudrate, timeout=1)
        self.lock = threading.Lock()
        self.crc16 = crcmod.mkCrcFun(0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000)
        self.ptt_status = False

    def build_packet(self, cmd_type, data: bytes):
        pkt = b'\xA5\xA5\xA5\xA5'
        body = bytes([cmd_type]) + data
        pktlen = len(body)
        pkt += bytes([pktlen]) + body
        crc = self.crc16(pkt[4:])
        pkt += struct.pack('>H', crc)
        return pkt

    def send_packet(self, pkt):
        with self.lock:
            self.ser.write(pkt)

    def set_freq(self, freq_hz: int):
        b = struct.pack('>II', freq_hz, freq_hz)
        pkt = self.build_packet(0x09, b)
        self.send_packet(pkt)

    def get_freq(self) -> int:
        pkt = self.build_packet(0x0B, b'')
        with self.lock:
            self.ser.write(pkt)
            response = self.ser.read(64)

        if not response.startswith(b'\xA5\xA5\xA5\xA5'):
            return 0
        try:
            payload = response[6:]
            vfoa_bytes = payload[4:8]  # 实际偏移需根据协议确认
            return struct.unpack('>I', vfoa_bytes)[0]
        except:
            return 0

    def set_mode(self, mode):
        if isinstance(mode, str):
            mode_id = self.mode_name_to_id(mode)
        else:
            mode_id = mode
        b = struct.pack('BB', mode_id, mode_id)
        pkt = self.build_packet(0x0A, b)
        self.send_packet(pkt)

    def get_mode(self) -> str:
        pkt = self.build_packet(0x0B, b'')
        with self.lock:
            self.ser.write(pkt)
            response = self.ser.read(64)

        if not response.startswith(b'\xA5\xA5\xA5\xA5'):
            return 'USB 2400'

        try:
            payload = response[6:]
            mode_id = payload[0]
            return f'{self.mode_id_to_name(mode_id)} 2400'
        except:
            return 'USB 2400'

    def set_ptt(self, ptt_on: bool):
        b = bytes([0x00 if ptt_on else 0x01])
        pkt = self.build_packet(0x07, b)
        self.send_packet(pkt)
        self.ptt_status = ptt_on

    def mode_name_to_id(self, name: str) -> int:
        name = name.upper()
        for id_, s in self.MODE_MAP.items():
            if s == name:
                return id_
        return 0

    def mode_id_to_name(self, mode_id: int) -> str:
        return self.MODE_MAP.get(mode_id, 'USB')

def select_serial_port():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("未检测到任何串口设备。")
        sys.exit(1)
    print("检测到以下串口设备：")
    for i, port in enumerate(ports):
        print(f"{i + 1}: {port.device} - {port.description}")
    while True:
        try:
            index = int(input("请选择串口编号：")) - 1
            if 0 <= index < len(ports):
                return ports[index].device
            else:
                print("无效编号，请重新输入。")
        except ValueError:
            print("请输入数字编号。")

def rigctl_server(bridge: PMR171Bridge, host='127.0.0.1', port=4532):

    def handle(client_socket):
        def handle_cmd(cmd):
            command_map = {
                'v': lambda: client_socket.send(b'PMR-171\n'),
                'V': lambda: client_socket.send(b'1.0.0\n'),
                'f': lambda: client_socket.send(f"{bridge.get_freq()}\n".encode()),
                'm': lambda: client_socket.send(f"{bridge.get_mode()}\n".encode()),
                't': lambda: client_socket.send(b'1\n' if bridge.ptt_status else b'0\n'),
            }
            if cmd in command_map:
                command_map[cmd]()
            elif cmd.startswith('F'):
                try:
                    freq = int(cmd[1:].strip())
                    bridge.set_freq(freq)
                    client_socket.send(b'RPRT 0\n')
                except:
                    client_socket.send(b'RPRT -1\n')
            elif cmd.startswith('M'):
                try:
                    _, mode, width = cmd.split()
                    bridge.set_mode(mode)
                    client_socket.send(b'RPRT 0\n')
                except:
                    client_socket.send(b'RPRT -1\n')
            elif cmd.startswith('T'):
                state = cmd[1:].strip()
                bridge.set_ptt(state == '1')
                client_socket.send(b'RPRT 0\n')
            else:
                client_socket.send(b'RPRT -1\n')

        with client_socket:
            while True:
                try:
                    cmd = client_socket.recv(128).decode().strip()
                    if not cmd:
                        break
                    if cmd == 'q':
                        break
                    handle_cmd(cmd)
                except Exception as e:
                    print(f"命令处理异常: {e}")
                    break

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen()
        print(f"rigctl 中转服务启动于 {host}:{port}")
        while True:
            try:
                conn, _ = s.accept()
                threading.Thread(target=handle, args=(conn,), daemon=True).start()
            except Exception as e:
                print(f"Socket异常: {e}")

def main():
    def signal_handler(sig, frame):
        print("\n程序已退出。")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    serial_port = select_serial_port()
    bridge = PMR171Bridge(serial_port)
    rigctl_server(bridge)

if __name__ == '__main__':
    main()
