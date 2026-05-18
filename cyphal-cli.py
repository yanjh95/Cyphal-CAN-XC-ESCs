import importlib
ws_module = importlib.import_module("pyka-can.ws-module")
import time
import sys
import os
import tty
import termios
import select
import argparse
from tabulate import tabulate
cyphal_module = importlib.import_module("pyka-can.cyphal")
Cyphal = cyphal_module.Cyphal
ESC = cyphal_module.ESC
ESCTracker = cyphal_module.ESCTracker
start_tx_thread = cyphal_module.start_tx_thread

def flash_mode(ws, tracker, target_id=None):
    print("=== ESC Node ID Flashing Mode ===")
    print("Passively listening for 2.0 seconds to dynamically discover local ESCs...")
    
    ws.start()
    
    # Discovery Phase
    start_listen = time.time()
    while time.time() - start_listen < 2.0:
        msg = ws.get_message(timeout=0.1)
        if msg:
            tracker.update_esc(Cyphal.decode(msg))
            
    active_escs = list(tracker.escs.values())
    
    if len(active_escs) == 0:
        print("\n[ERROR] No ESC detected. Please check the UART connection and ESC power rail.")
        ws.stop()
        sys.exit(1)
        
    if len(active_escs) > 1:
        print(f"\n[CRITICAL ERROR] Safety Lockout Activated: {len(active_escs)} ESCs physically detected on the bus!")
        print("To prevent mass-flashing collisions, you must disconnect all but the ONE specific ESC you wish to flash.")
        ws.stop()
        sys.exit(1)
        
    target_esc = active_escs[0]
    print(f"\n✅ Single ESC Secured. Current Node ID: {hex(target_esc.node_id)}")
    
    if target_id is None:
        try:
            user_in = input(f"Enter NEW NODE ID in hex (e.g. 0x20): ").strip()
            if not user_in:
                print("Aborted by user.")
                ws.stop()
                sys.exit(0)
                
            new_id = int(user_in, 16)
            if new_id < 0 or new_id > 255:
                raise ValueError
        except ValueError:
            print("[ERROR] Invalid format! Must be valid Hexadecimal (Example: 0x20)")
            ws.stop()
            sys.exit(1)
    else:
        try:
            new_id = int(target_id, 16)
        except ValueError:
            print(f"[ERROR] Invalid Hex format provided in --flash: {target_id}")
            ws.stop()
            sys.exit(1)
        
    print(f"\n[!] WARNING: You are permanently flashing Node ID {hex(new_id)}")
    if target_id is None:
        confirm = input("Type 'YES' to proceed: ")
        if confirm != "YES":
            print("Aborted.")
            ws.stop()
            sys.exit(1)
    
    # Payload 1: Broadcast setup lock
    ws.send_message(0x0C78000F, [0x01, 0xFF, 0x00, 0x00, 0xE2], is_extended=True)
    time.sleep(0.1)
    
    # Payload 2: Set New ID instruction
    ws.send_message(0x1078010F, [0x00, new_id, 0xE2], is_extended=True)
    
    print("Flash packets instantly transmitted! Waiting for ESC to reboot and verify...")
    
    # Wipe the tracker clean
    tracker.escs.clear()
    
    # Verification Phase 
    start_verify = time.time()
    success = False
    while time.time() - start_verify < 5.0:
        msg = ws.get_message(timeout=0.1)
        if msg:
            tracker.update_esc(Cyphal.decode(msg))
            if new_id in tracker.escs:
                success = True
                break
                
    ws.stop()
    
    if success:
        print(f"\n✅ Flashing Successful! ESC is now officially responding as {hex(new_id)} \n")
    else:
        print(f"\n⚠️ FAILED: Did not see an ESC with Node ID {hex(new_id)} resume telemetry within 5.0 seconds. \n")


def read_registers_mode(ws, tracker):
    print("=== Cyphal ESC Register Reader ===")
    print("Passively listening for 1.5 seconds to discover local ESCs...")
    
    ws.start()
    
    # Discovery Phase
    start_listen = time.time()
    while time.time() - start_listen < 1.5:
        msg = ws.get_message(timeout=0.1)
        if msg:
            decoded = Cyphal.decode(msg)
            if not decoded.get("is_service"):
                tracker.update_esc(decoded)
            
    active_escs = list(tracker.escs.values())
    if not active_escs:
        print("\n[ERROR] No ESC telemetry detected on the bus!")
        ws.stop()
        sys.exit(1)
        
    print(f"\nDiscovered {len(active_escs)} ESC(s). Silencing bus to guarantee Service stability...")
    
    # Command 6144 (Broadcast): Node "255" means All ESCs respond.
    # Command "1" = Disable all telemetry except Heartbeat. This frees up 99% of CAN Bus bandwidth!
    ws.send_message(0x0C78000F, [0x01, 0xFF, 0x00, 0x00, 0xE0], is_extended=True)
    time.sleep(0.5) # Wait for silence
    
    # Aggressively flush the UART queue of any stale telemetry packets
    while not ws.rx_queue.empty():
        try:
            ws.rx_queue.get_nowait()
        except:
            pass
            
    print("Querying registers sequentially...\n")
    
    regs = [
        (0x00, "Protocol Version"),
        (0x01, "Hardware Version"),
        (0x02, "Software Version"),
        (0x03, "Auto-Upload Mask"),
        (0x04, "CAN Baud Rate Cd"),
        (0x05, "Updater Process ")
    ]
    
    sequence_byte_table = {}
    
    for esc in active_escs:
        print(f"--- ESC Node {hex(esc.node_id)} ----------------")
        sequence_byte_table[esc.node_id] = 0xE0
        
        for reg_idx, reg_name in regs:
            # Construct Service 256 Request CAN ID: Priority(2), Service(1), Request(1), ServiceID(256), Dest(esc.node_id), Source(127)
            pri = 2
            is_svc = 1
            is_req = 1
            sid = 256
            dest = esc.node_id
            src = 127
            can_id = (pri << 26) | (is_svc << 25) | (is_req << 24) | (0 << 23) | (sid << 14) | (dest << 7) | src
            
            # Request Type A Payload: Operation(0), Index(reg_idx), TailByte(Seq)
            payload = [0x00, reg_idx, sequence_byte_table[esc.node_id]]
            
            ws.send_message(can_id, payload, is_extended=True)
            sequence_byte_table[esc.node_id] = 0xE0 if sequence_byte_table[esc.node_id] >= 0xFF else sequence_byte_table[esc.node_id] + 1
            
            # Wait precisely for matching response
            wait_start = time.time()
            got_response = False
            while time.time() - wait_start < 0.25:
                msg = ws.get_message(timeout=0.05)
                if not msg: continue
                
                decoded = Cyphal.decode(msg)
                if decoded.get("is_service") and decoded.get("service_id") == 256 and not decoded.get("is_request"):
                    if decoded.get("node_id") == esc.node_id:
                        reg_data = decoded.get("register_256", {})
                        if reg_data.get("index") == reg_idx:
                            state = reg_data.get("state")
                            val = reg_data.get("val")
                            if state == 0x10:
                                meaning = ""
                                if reg_idx in [0x00, 0x01, 0x02]:
                                    meaning = f"V{val//100}.{(val%100)//10}.{val%10}"
                                elif reg_idx == 0x03:
                                    bits = []
                                    if val & 0x01: bits.append("HB_7509")
                                    if val & 0x02: bits.append("INFO_6160")
                                    if val & 0x04: bits.append("INFO_6161")
                                    meaning = " | ".join(bits) if bits else "Disabled"
                                elif reg_idx == 0x04:
                                    meaning = {4:"250K", 5:"500K(default)", 6:"800K", 7:"1000K"}.get(val, "Unknown")
                                elif reg_idx == 0x05:
                                    meaning = {
                                        0: "non-upgrade", 0x20: "wait comm", 0x30: "copy comm",
                                        0x40: "wait APP", 0xA0: "COM ok", 0xA1: "APP ok",
                                        0xE0: "FAIL SIZE", 0xE1: "FAIL CODE"
                                    }.get(val, "Unknown")
                                    
                                print(f"[{hex(reg_idx)}] {reg_name} : {val:<5} (0x{val:04X}) -> {meaning}")
                            else:
                                print(f"[{hex(reg_idx)}] {reg_name} : FAILED  (Error State {hex(state)})")
                            got_response = True
                            break
                            
            if not got_response:
                print(f"[{hex(reg_idx)}] {reg_name} : TIMEOUT")
                
            time.sleep(0.01) 
        print("") # spacing
            
    # Restore normal telemetry broadcast (Command: 100) -> 0x64
    print("Re-enabling standard telemetry broadcasts...")
    ws.send_message(0x0C78000F, [0x64, 0xFF, 0x00, 0x00, 0xE0], is_extended=True)
    time.sleep(0.1)
    
    print("✅ Register Polling Successfully Complete.")
    ws.stop()
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Interactive Cyphal ESC Controller")
    parser.add_argument('--port', '-p', type=str, default='/dev/ttyUSB0', help='UART Port')
    parser.add_argument('--baud', '-b', type=int, default=230400, help='Baud rate')
    parser.add_argument('--flash', nargs='?', const='prompt', type=str, help='Enter single-ESC ID Flashing Mode. Provide HEX_ID to skip prompt')
    parser.add_argument('--read-registers', action='store_true', help='Query and print versions and configurations of all ESCs')
    args = parser.parse_args()

    ws = ws_module.WSModule(args.port, args.baud)
    tracker = ESCTracker()
    
    if args.flash is not None:
        if args.flash == 'prompt':
            flash_mode(ws, tracker, target_id=None)
        else:
            flash_mode(ws, tracker, target_id=args.flash)
        sys.exit(0)
        
    if args.read_registers:
        read_registers_mode(ws, tracker)
        sys.exit(0)
    
    global_throttles = {} 
    
    start_tx_thread(ws, global_throttles)
    last_print_time = time.time()

    selected_esc_index = 0
    control_all = False
    input_buffer = ""
    pie_frame_counter = 0

    try:
        ws.start()
        
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            exit_requested = False
            
            while not exit_requested:
                
                if select.select([sys.stdin], [], [], 0.0)[0]:
                    data = os.read(sys.stdin.fileno(), 4)
                    if data:
                        escs_count = len(tracker.escs)
                        max_idx = max(0, escs_count - 1)
                        
                        if data in (b'q', b'Q'):
                            exit_requested = True
                            break
                        elif data in (b'a', b'A'):
                            control_all = not control_all
                        elif data == b'\x1b[A': # UP
                            selected_esc_index = max(0, selected_esc_index - 1)
                        elif data == b'\x1b[B': # DOWN
                            selected_esc_index = min(max_idx, selected_esc_index + 1)
                        elif data == b'\x1b[C': # RIGHT (Increase)
                            targets = list(tracker.escs.values()) if control_all else \
                                      [sorted(tracker.escs.values(), key=lambda x: x.node_id)[selected_esc_index]] if tracker.escs else []
                            for e in targets:
                                if e.node_id in global_throttles:
                                    global_throttles[e.node_id] = min(2048, global_throttles[e.node_id] + 1)
                        elif data == b'\x1b[D': # LEFT (Decrease)
                            targets = list(tracker.escs.values()) if control_all else \
                                      [sorted(tracker.escs.values(), key=lambda x: x.node_id)[selected_esc_index]] if tracker.escs else []
                            for e in targets:
                                if e.node_id in global_throttles:
                                    global_throttles[e.node_id] = max(0, global_throttles[e.node_id] - 1)
                        elif data in (b'\r', b'\n'): # ENTER
                            if input_buffer:
                                try:
                                    val = int(input_buffer)
                                    targets = list(tracker.escs.values()) if control_all else \
                                              [sorted(tracker.escs.values(), key=lambda x: x.node_id)[selected_esc_index]] if tracker.escs else []
                                    for e in targets:
                                        if e.node_id in global_throttles:
                                            global_throttles[e.node_id] = min(2048, max(0, val))
                                except ValueError:
                                    pass
                                input_buffer = ""
                        elif data in (b'\x7f', b'\x08'): # BACKSPACE
                            input_buffer = input_buffer[:-1]
                        else:
                            try:
                                char_str = data.decode('ascii')
                                if char_str.isdigit() and len(input_buffer) < 4:
                                    input_buffer += char_str
                            except UnicodeDecodeError:
                                pass

                # --- Read Hardware ---
                msg = ws.get_message(timeout=0.01)
                if msg is not None:
                    tracker.update_esc(Cyphal.decode(msg))

                # --- DYNAMIC CULLING & SYNC ---
                now = time.time()
                dead_nodes = []
                for node_id, esc_obj in list(tracker.escs.items()):
                    if now - esc_obj.last_message_time > 2.0:
                        dead_nodes.append(node_id)
                    else:
                        if node_id not in global_throttles:
                             global_throttles[node_id] = 0
                            
                for dead_node in dead_nodes:
                    del tracker.escs[dead_node]
                    if dead_node in global_throttles:
                        del global_throttles[dead_node]

                # --- TUI Rendering at 5Hz ---
                now = time.time()
                if now - last_print_time >= 0.2:
                    last_print_time = now
                    pie_frame_counter += 1
                    
                    escs = tracker.escs
                    sorted_escs = sorted(escs.values(), key=lambda x: x.node_id)
                    
                    if selected_esc_index >= len(sorted_escs):
                        selected_esc_index = max(0, len(sorted_escs) - 1)
                    
                    table_data = []
                    pie_frames = ['◴', '◷', '◶', '◵']
                    pie_idx = pie_frame_counter % 4
                    is_blink_on = int(now * 2) % 2 == 0
                    
                    for idx, e in enumerate(sorted_escs):
                        target_thr = global_throttles.get(e.node_id, 0)
                        
                        is_selected = control_all or (idx == selected_esc_index)
                        if is_selected:
                            blinker = "█" if is_blink_on else " "
                            display_thr = f"{input_buffer}_" if input_buffer else str(target_thr)
                        else:
                            blinker = " "
                            display_thr = str(target_thr)
                            
                        target_cell = f"{blinker} {display_thr}"
                        
                        healthy_mask = (1 << 15) | (1 << 13) | (1 << 3)
                        has_fault = (e.operating_status & ~healthy_mask) != 0
                        
                        if has_fault:
                            pie_color = "\033[91m"
                            p_char = pie_frames[pie_idx] if e.physical_rpm > 0 else '○'
                        elif e.physical_rpm > 0:
                            pie_color = "\033[92m"
                            p_char = pie_frames[pie_idx]
                        else:
                            pie_color = "\033[97m"
                            p_char = '○'
                            
                        pie_char = f"{pie_color}{p_char}\033[0m"
                        
                        table_data.append([
                            pie_char, hex(e.node_id), e.power_on_time, target_cell, e.out_throttle, 
                            e.physical_rpm, f"{e.bus_voltage:.1f}", f"{e.bus_current:.1f}", 
                            e.MOS_temp, e.operating_status
                        ])
                        
                    headers = ["Spin", "Node ID", "Time(s)", "Target Thr", "Real Thr", "RPM", "Voltage(V)", "Current(A)", "MOS(C)", "Status"]
                    aligns = ["center", "center", "right", "right", "right", "right", "right", "right", "right", "left"]
                    
                    sys.stdout.write("\033[H\033[J")
                    sys.stdout.write("=== Interactive Cyphal Throttle Controller ===\n")
                    mode_txt = "ALL ESCs" if control_all else "SINGLE"
                    sys.stdout.write(f"Keys: [Up/Down] Select ESC | [Left/Right] Trim Thr | [Type Num + Enter] Set Thr | [A] Mode ({mode_txt})\n\n")
                    
                    if not escs:
                        sys.stdout.write("Waiting for ESC telemetry (Please ensure ESCs are powered)...\n\n")
                    else:
                        sys.stdout.write(tabulate(table_data, headers=headers, tablefmt="grid", colalign=aligns) + "\n\n")
                    
                    sys.stdout.write(">>> PRESS 'q' TO QUIT <<<\n")
                    sys.stdout.flush()
                    
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\nStopping Test & Sending Zero Throttle...\n")
        for can_id in [0x0c78080f, 0x0c78090f, 0x0c780a0f]:
             ws.send_message(can_id, [0x00]*8, is_extended=True)
             time.sleep(0.05)
             
        ws.stop()

if __name__ == "__main__":
    main()
