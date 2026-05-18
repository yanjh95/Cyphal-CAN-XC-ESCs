import importlib
ws_module = importlib.import_module("pyka-can.ws-module")
import time
import threading

class Cyphal:
    @staticmethod
    def decode(raw_msg):
        is_service = bool(raw_msg.can_id & (1 << 25))
        
        # 1. Parse IDs from the 29-bit extended CAN ID
        if not is_service:
            node_id = raw_msg.can_id & 0x7F
            subject_id = (raw_msg.can_id & 0x001FFF00) >> 8
            is_request = False
            service_id = 0
        else:
            node_id = raw_msg.can_id & 0x7F # Source Node-ID
            is_request = bool(raw_msg.can_id & (1 << 24))
            service_id = (raw_msg.can_id >> 14) & 0x1FF
            subject_id = 0
            
        # 2. Extract payload to Little-Endian 64-bit integer 
        # (Standard Cyphal practice for bit extraction)
        padded_data = bytearray(8)
        padded_data[:len(raw_msg.data)] = raw_msg.data
        payload = int.from_bytes(padded_data, byteorder="little")
        
        # 3. Initialize results dictionary
        result = {"node_id": node_id, "is_service": is_service}
        
        # ==========================================
        # Broadcast Messages [subject_id]
        # ==========================================
        if not is_service:
            result["subject_id"] = subject_id
            if subject_id == 6160:
                electrical_speed = payload & 0xFFFF
                result["electrical_speed"] = electrical_speed
                result["physical_rpm"] = int(electrical_speed * 0.42857 * 2)
                
                current_raw = (payload >> 16) & 0xFFFF
                if current_raw & 0x8000:
                    current_raw -= 0x10000
                result["bus_current"] = current_raw / 10.0
                
                result["operating_status"] = (payload >> 32) & 0xFFFF
                
            elif subject_id == 6161:
                result["out_throttle"] = payload & 0xFFFF
                result["bus_voltage"] = ((payload >> 16) & 0xFFFF) / 10.0
                result["MOS_temp"] = ((payload >> 32) & 0xFF) - 40
                result["Cap_temp"] = ((payload >> 40) & 0xFF) - 40
                result["Motor_temp"] = ((payload >> 48) & 0xFF) - 40

            elif subject_id == 7509:
                result["power_on_time"] = payload & 0xFFFFFFFF
                result["node_health_status"] = (payload >> 36) & 0xFF
                result["node_current_mode"] = (payload >> 42) & 0xFF
                
            # Future/Reserved Broadcast Subjects
            elif subject_id == 6144: # Command Control
                pass
            elif subject_id == 6145: # Device ID address setting
                pass
            elif 6152 <= subject_id <= 6159: # Throttle Transmission
                pass
                
        # ==========================================
        # Service Messages [service_id]
        # ==========================================
        else:
            result["service_id"] = service_id
            result["is_request"] = is_request
            
            # Register Read/Write [256]
            if service_id == 256 and not is_request:
                op_state = payload & 0xFF
                index = (payload >> 8) & 0xFF
                reg_val = (payload >> 16) & 0xFFFF
                
                # Cyphal 256 returns 'state' 0x10 for success
                result["register_256"] = {
                    "state": op_state,
                    "index": index,
                    "val": reg_val
                }
                
            # Node Information [430]
            elif service_id == 430 and not is_request:
                result["protocol_ver"] = payload & 0xFFFF
                result["hardware_ver"] = (payload >> 16) & 0xFFFF
                result["software_ver"] = (payload >> 32) & 0xFFFF
                
            # Service Order Control [435]
            elif service_id == 435 and not is_request:
                result["command_status"] = payload & 0xFF

        return result

    @staticmethod
    def encode_throttles(throttle_dict, sequence_byte):
        packets = []
        # Cyphal mappings: max 12 ESCs grouped tightly in blocks of 4
        blocks = [
            (0x0c78080f, [0x10, 0x11, 0x12, 0x13]), # Subject 6152
            (0x0c78090f, [0x14, 0x15, 0x16, 0x17]), # Subject 6153
            (0x0c780a0f, [0x18, 0x19, 0x1A, 0x1B])  # Subject 6154
        ]
        
        current_seq = sequence_byte
        
        for can_id, node_list in blocks:
            # Graceful networking: Skip broadcasting empty 4-blocks to save heavy bus bandwidth!
            if not any(node in throttle_dict for node in node_list):
                continue
                
            t = []
            for node in node_list:
                val = throttle_dict.get(node, 0)
                t.append(min(max(int(val), 0), 2048)) # Safely constrain to 11-bit limits
                
            tb = [val.to_bytes(2, 'little') for val in t]
            data = bytearray(8)
            
            # The cursed 11-bit overlapping Cyphal compression format natively abstracted:
            data[0] = tb[0][0]
            data[1] = tb[0][1]
            data[2] = tb[1][0]
            data[3] = tb[1][1]
            data[4] = tb[2][0]
            data[5] = tb[2][1]
            data[6] = tb[3][0]

            temp = tb[3][1]
            data[5] |= (temp & 0b11) << 6
            data[3] |= ((temp >> 2) & 0b11) << 6
            data[1] |= ((temp >> 4) & 0b11) << 6
            
            data[7] = current_seq
            packets.append((can_id, list(data)))
            
            # Roll Cyphal standard multiplexing sequence
            current_seq = 0xE0 if current_seq >= 0xFF else current_seq + 1
            
        return packets, current_seq


class ESC:
    def __init__(self, node_id):
        self.node_id = node_id
        self.last_message_time = time.time()
        self.dropout_count = 0
        self.power_on_time = 0
        self.node_health_status = 0
        self.node_current_mode = 0
        self.out_throttle = 0
        self.bus_voltage = 0.0
        self.MOS_temp = 0
        self.Cap_temp = 0
        self.Motor_temp = 0
        self.electrical_speed = 0
        self.physical_rpm = 0
        self.bus_current = 0.0
        self.operating_status = 0

    def __repr__(self):
        hex_id = f"0x{self.node_id:02X}"
        return (f"<ESC {hex_id} | Time:{self.power_on_time}s | Mode:{self.node_current_mode} | Health:{self.node_health_status} | "
                f"Stat:{self.operating_status} | Thr:{self.out_throttle} | RPM:{self.physical_rpm} (Elec:{self.electrical_speed}) | "
                f"{self.bus_voltage:.1f}V {self.bus_current:.1f}A | "
                f"MOS:{self.MOS_temp}°C Cap:{self.Cap_temp}°C Mot:{self.Motor_temp}°C>")
    def get_status_str(self):
        flags = []
        s = self.operating_status
        if s == 0 or s == (1 << 15) or s == ((1 << 15) | (1 << 13) | (1 << 3)):
            return "OK"
        if s & (1 << 0): flags.append("RunStatus")
        if s & (1 << 1): flags.append("Reverse")
        if s & (1 << 2): flags.append("Reserved_2")
        if s & (1 << 3): flags.append("Armed")
        if s & (1 << 4): flags.append("Reserved_4")
        if s & (1 << 5): flags.append("Reserved_5")
        if s & (1 << 6): flags.append("OverVoltage")
        if s & (1 << 7): flags.append("UnderVoltage")
        if s & (1 << 8): flags.append("OverCurrent")
        if s & (1 << 9): flags.append("OverTemp")
        if s & (1 << 10): flags.append("MotorStall")
        if s & (1 << 11): flags.append("PhaseLoss")
        if s & (1 << 12): flags.append("HardwareFault")
        if s & (1 << 13): flags.append("CommNormal")
        if s & (1 << 14): flags.append("Calibrating")
        if s & (1 << 15): flags.append("Standby")
        
        filtered = [f for f in flags if f not in ("CommNormal", "Standby", "RunStatus", "Armed")]
        return "|".join(filtered) if filtered else "OK"



class ESCTracker:
    def __init__(self):
        self.escs = {}

    def update_esc(self, decoded_dict):
        # 1. We must always know who we are talking to
        node_id = decoded_dict.get("node_id")
        if node_id is None:
            return 
            
        # 2. If this is a brand new ESC we haven't seen before, create an empty memory for it
        if node_id not in self.escs:
            self.escs[node_id] = ESC(node_id)
            
        # 3. Target the specific ESC object in memory
        target = self.escs[node_id]
        target.last_message_time = time.time()
        
        # 4. Patch only the data that actually arrived in this exact packet
        # Efficiently iterate through the dictionary and update matching object attributes
        for key, value in decoded_dict.items():
            if hasattr(target, key):
                setattr(target, key, value)




def start_tx_thread(ws, throttle_dict):
    def tx_loop():
        sequence_byte = 0xE0
        while True: # Infinite Daemon safely dies when Python main script terminates
            try:
                packets, sequence_byte = Cyphal.encode_throttles(throttle_dict, sequence_byte)
                for can_id, payload in packets:
                    ws.send_message(can_id, payload, is_extended=True)
            except Exception:
                pass
            time.sleep(0.1) # Cyphal dictates exactly 10Hz throttle heartbeat requirement
            
    threading.Thread(target=tx_loop, daemon=True).start()
