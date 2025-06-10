# --- monitor_espnow6 ---

import socket, re, threading, time, uuid
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button

# --- UDP ---
UDP_PORT = 12345
pattern_cmd = re.compile(r'<([0-9A-F:]{17})>\s+CMD:(\w+)(?:\s+([0-9A-F:]{17}))?(?:\s+(.*))?')
pattern_recv = re.compile(r'RECEIVED\s+([0-9A-F:]{17})\s+([0-9A-F:]{17})')
pattern_route_delivered = re.compile(r'ROUTE_DELIVERED\s+([0-9A-F:]{17})\s+([0-9A-F:]{17})\s+([\w-]+)\s+(.*)') # final_dest prev_hop msg_id payload
pattern_ack_route_step_rcvd = re.compile(r'ACK_ROUTE_STEP_RECEIVED\s+([0-9A-F:]{17})\s+([\w-]+)') # self_mac msg_id
pattern_ack_route_espnow_sent = re.compile(r'ACK_ROUTE_ESPNOW_SENT\s+([0-9A-F:]{17})\s+([0-9A-F:]{17})\s+([\w-]+)') # self_mac sent_to_mac msg_id


# --- Grafo y estado ---
G = nx.Graph()
lock = threading.Lock()
mac_ip = {}      # MAC -> (ip,port)
edge_colors = {} # (u,v)->color
pos = {}
recompute = True
selected = []
active_routes_viz = {} # msg_id -> {'path': [(u,v), ...], 'color': 'purple', 'status': 'pending'}

# Inicializar nodo ALL
G.add_node('ALL')

# --- Socket UDP ---
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.bind(('', UDP_PORT))

def handle_interaction(src, dst):
    if src not in mac_ip:
        print(f"[!] No conozco la IP de {src}")
        return
    cmd = f"UNICAST {dst} datos_test\n".encode()
    sock.sendto(cmd, mac_ip[src])
    with lock:
        if G.has_node(src) and G.has_node(dst):
            G.add_edge(src, dst)
            edge_colors[tuple(sorted((src, dst)))] = 'black' # Usar tupla ordenada
            # edge_colors[(dst, src)] = 'black' # No es necesario para grafos no dirigidos si la clave es ordenada
            print(f"[>] Intentando enviar datos de {src} a {dst}. Arista negra añadida.")
        else:
            print(f"[!] Error: Nodos {src} o {dst} no encontrados.")


def on_click(event):
    global recompute, selected
    if event.inaxes == ax_dfs_button or event.inaxes == ax_bfs_button:
        return
    if event.inaxes is None or event.inaxes != ax_main:
        return

    clicked_node = None
    with lock:
        current_pos = dict(pos)
    min_dist_sq = 0.0025
    for n, (x_node, y_node) in current_pos.items():
        if n == 'ALL': continue
        if event.xdata is None or event.ydata is None:
            continue
        dist_sq = (x_node - event.xdata)**2 + (y_node - event.ydata)**2
        if dist_sq < min_dist_sq:
            clicked_node = n
            break
    if clicked_node:
        if clicked_node not in selected:
            if len(selected) < 2:
                selected.append(clicked_node)
                print("Seleccionado:", clicked_node, "- Nodos seleccionados:", selected)
            else:
                print(f"Ya hay 2 nodos seleccionados: {selected}. Deselecciona o inicia ruta.")
        else:
            selected.remove(clicked_node)
            print("Deseleccionado:", clicked_node, "- Nodos seleccionados:", selected)
        recompute = True


def remove_edge_or_mark_failed(u, v, failed=False, msg_id=None):
    global recompute
    with lock:
        if not G.has_node(u) or not G.has_node(v):
            print(f"[!] Intento de operar en arista ({u}-{v}) pero uno o ambos nodos no existen en G.")
            return
        if not G.has_edge(u,v):
            G.add_edge(u,v)
            print(f"[i] Arista ({u}-{v}) no existía, añadida para marcar estado.")

        color = 'red' if failed else 'green'
        edge_colors[tuple(sorted((u,v)))] = color
        status_msg = "Falló" if failed else "Éxito"
        print(f"[{'+' if not failed else '-'}] {status_msg} en comunicación entre {u} y {v}.")
        if not failed:
            is_part_of_active_route = False
            for route_id, route_data in active_routes_viz.items():
                if isinstance(route_data, dict) and 'path' in route_data:
                    if tuple(sorted((u,v))) in route_data['path']:
                        is_part_of_active_route = True
                        break
            if not is_part_of_active_route:
                threading.Timer(1.0, lambda: _remove_successful_edge(u,v)).start()
        recompute = True

def _remove_successful_edge(u,v):
    global recompute
    edge_key = tuple(sorted((u,v)))
    with lock:
        if G.has_edge(u,v) and edge_colors.get(edge_key) == 'green':
            G.remove_edge(u,v)
            edge_colors.pop(edge_key, None)
            print(f"[–] Arista removida entre {u} y {v} tras confirmación.")
            recompute = True


def _execute_routed_send(path, msg_id, payload, route_type="DFS"):
    global active_routes_viz, recompute
    print(f"Ejecutando ruta {route_type} ID: {msg_id}, Path: {path}, Payload: {payload}")
    path_edges_for_viz = []
    for i in range(len(path) - 1):
        path_edges_for_viz.append(tuple(sorted((path[i], path[i+1]))))
    with lock:
        active_routes_viz[msg_id] = {'path': path_edges_for_viz, 'color': 'purple', 'status': 'pending', 'steps_acked': 0}
        recompute = True
    for i in range(len(path) - 1):
        sender_mac = path[i]
        receiver_mac = path[i+1]
        final_dest_mac = path[-1]
        if sender_mac not in mac_ip:
            print(f"[!] Error en ruta {msg_id}: No conozco la IP de {sender_mac}. Abortando ruta.")
            with lock:
                if msg_id in active_routes_viz: # Chequear si aún existe
                    active_routes_viz[msg_id]['status'] = 'failed_no_ip'
                    active_routes_viz[msg_id]['color'] = 'maroon'
                recompute = True
            return
        cmd_for_udp = f"ROUTE_STEP {receiver_mac} {final_dest_mac} {msg_id} {payload}\n".encode()
        print(f"[ROUTE {msg_id}] Enviando UDP a {sender_mac} ({mac_ip.get(sender_mac)}) para que envíe a {receiver_mac} (Destino final: {final_dest_mac})")
        sock.sendto(cmd_for_udp, mac_ip[sender_mac])
        time.sleep(0.2)


def handle_route_request(route_type_name):
    global selected, recompute, G
    if len(selected) != 2:
        print(f"[!] Para ruta {route_type_name}, selecciona un nodo de INICIO y uno de FIN.")
        return

    start_node, end_node = selected[0], selected[1]
    print(f"Calculando ruta {route_type_name} de {start_node} a {end_node}...")
    print(f"NOTA: Si experimentas 'AttributeError' para dfs_path o bfs_path, considera actualizar networkx: pip install --upgrade networkx")

    path = None
    pathfinding_exception = None

    with lock:
        current_G_nodes = [n for n in G.nodes() if n != 'ALL']
        if not current_G_nodes or start_node not in current_G_nodes or end_node not in current_G_nodes:
            print("[!] Nodos de inicio o fin no válidos o no hay nodos ESP en el grafo actual.")
            selected.clear()
            recompute = True
            return
        H = G.subgraph(current_G_nodes).copy()

    # --- Bloque de Depuración para el subgrafo H ---
    print(f"\n--- [DEBUG RUTA {route_type_name}] ---")
    print(f"Subgrafo H para ruta de {start_node} a {end_node}:")
    print(f"Nodos en H: {list(H.nodes())}")
    print(f"Aristas en H: {list(H.edges())}")
    if H.has_node(start_node) and H.has_node(end_node):
        print(f"Chequeo general nx.has_path(H, {start_node}, {end_node}): {nx.has_path(H, start_node, end_node)}")
    else:
        print(f"Start_node ({start_node}) o end_node ({end_node}) no está en H.")
    print(f"--- [FIN DEBUG RUTA {route_type_name}] ---\n")
    # --- Fin Bloque de Depuración ---

    try:
        if not H.has_node(start_node):
            raise nx.NodeNotFound(f"Nodo de inicio {start_node} no encontrado en el subgrafo de nodos ESP.")
        if not H.has_node(end_node):
            raise nx.NodeNotFound(f"Nodo de fin {end_node} no encontrado en el subgrafo de nodos ESP.")

        if route_type_name == "DFS":
            try:
                path = nx.dfs_path(H, source=start_node, target=end_node)
            except AttributeError:
                print("[i] nx.dfs_path no encontrado, usando alternativa basada en predecesores (NetworkX < 2.6).")
                if start_node == end_node:
                    path = [start_node]
                else:
                    pred = nx.dfs_predecessors(H, source=start_node)
                    if end_node not in pred and end_node != start_node :
                         raise nx.NetworkXNoPath(f"No hay camino DFS de {start_node} a {end_node} (end_node no en predecesores).")
                    path_reconstructed = [end_node]
                    curr = end_node
                    while curr != start_node:
                        if curr not in pred:
                            raise nx.NetworkXNoPath(f"Camino DFS interrumpido al reconstruir desde {end_node} a {start_node} en {curr}.")
                        curr = pred[curr]
                        path_reconstructed.append(curr)
                    path = path_reconstructed[::-1]
        elif route_type_name == "BFS":
            try:
                path = nx.bfs_path(H, source=start_node, target=end_node)
            except AttributeError:
                print("[i] nx.bfs_path no encontrado, usando nx.shortest_path como alternativa (NetworkX < 2.6).")
                path = nx.shortest_path(H, source=start_node, target=end_node)
    except nx.NetworkXNoPath as e:
        pathfinding_exception = e
        print(f"[!] No se encontró ruta {route_type_name} entre {start_node} y {end_node}: {e}")
    except nx.NodeNotFound as e:
        pathfinding_exception = e
        print(f"[!] Nodo no encontrado en el subgrafo para la ruta {route_type_name}: {e}")
    except Exception as e:
        pathfinding_exception = e
        print(f"[!] Error inesperado al calcular la ruta {route_type_name}: {e}")

    if path:
        print(f"[*] Ruta {route_type_name} encontrada: {path}")
        msg_id = str(uuid.uuid4())[:8]
        payload = f"Ruta{route_type_name}_{msg_id[:4]}"
        threading.Thread(target=_execute_routed_send, args=(path, msg_id, payload, route_type_name), daemon=True).start()
    else:
        print(f"[i] No se pudo enviar la ruta {route_type_name} debido a errores previos o no se encontró camino.")
    selected.clear()
    recompute = True


def on_dfs_button_clicked(event):
    handle_route_request("DFS")

def on_bfs_button_clicked(event):
    handle_route_request("BFS")


def listener():
    global recompute, G, mac_ip, edge_colors, active_routes_viz
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            line = data.decode().strip()
            print(f"[UDP_RX from {addr}] {line}")

            m_ack_step_rcvd = pattern_ack_route_step_rcvd.match(line)
            if m_ack_step_rcvd:
                node_mac, msg_id = m_ack_step_rcvd.groups()
                print(f"[ROUTE_ACK_UDP] Nodo {node_mac} recibió comando para ruta {msg_id}")
                with lock:
                    if msg_id in active_routes_viz:
                        active_routes_viz[msg_id]['status'] = f'step_rcvd_by_{node_mac}'
                continue

            m_ack_espnow_sent = pattern_ack_route_espnow_sent.match(line)
            if m_ack_espnow_sent:
                sender_mac, sent_to_mac, msg_id = m_ack_espnow_sent.groups()
                print(f"[ROUTE_ACK_ESPNOW] Nodo {sender_mac} envió ESP-NOW a {sent_to_mac} para ruta {msg_id}")
                edge_key = tuple(sorted((sender_mac, sent_to_mac)))
                with lock:
                    if msg_id in active_routes_viz:
                        active_routes_viz[msg_id]['status'] = f'espnow_sent_{sender_mac}_to_{sent_to_mac}'
                        active_routes_viz[msg_id]['steps_acked'] = active_routes_viz[msg_id].get('steps_acked',0) + 1
                        if G.has_edge(sender_mac, sent_to_mac): # Asegurarse que la arista existe
                                edge_colors[edge_key] = 'cyan'
                        else: # Si no existe, añadirla para colorear
                            G.add_edge(sender_mac,sent_to_mac)
                            edge_colors[edge_key] = 'cyan'
                        recompute = True
                continue

            m_route_delivered = pattern_route_delivered.match(line)
            if m_route_delivered:
                final_dest, prev_hop, msg_id, payload = m_route_delivered.groups()
                print(f"[ROUTE_DELIVERED] Mensaje {msg_id} llegó a {final_dest} desde {prev_hop}. Payload: {payload}")
                edge_key = tuple(sorted((prev_hop, final_dest)))
                with lock:
                    if msg_id in active_routes_viz:
                        active_routes_viz[msg_id]['status'] = 'delivered'
                        active_routes_viz[msg_id]['color'] = 'lime'
                        if G.has_edge(prev_hop, final_dest): # Asegurarse que la arista existe
                            edge_colors[edge_key] = 'lime'
                        else: # Si no existe, añadirla para colorear
                            G.add_edge(prev_hop, final_dest)
                            edge_colors[edge_key] = 'lime'
                        recompute = True
                        # Limpiar la ruta de la visualización después de un tiempo
                        threading.Timer(10.0, lambda mid: active_routes_viz.pop(mid, None), args=[msg_id]).start()
                continue

            m_recv = pattern_recv.match(line)
            if m_recv:
                receiver_mac, original_sender_mac = m_recv.groups()
                print(f"[<] Confirmación UNICAST: {receiver_mac} recibió de {original_sender_mac}")
                remove_edge_or_mark_failed(original_sender_mac, receiver_mac, failed=False)
                continue

            m_cmd = pattern_cmd.search(line)
            if m_cmd:
                mac_origin, cmd_type, target_mac_info, cmd_payload = m_cmd.groups()
                mac_ip[mac_origin] = addr

                with lock:
                    if not G.has_node(mac_origin):
                        G.add_node(mac_origin)
                        print(f"[+] Nuevo nodo añadido: {mac_origin} (IP: {addr})")
                        recompute = True
                    
                    # Si target_mac_info (ej. el emisor de un broadcast ESP-NOW) está presente, añadirlo también si no existe
                    if target_mac_info and not G.has_node(target_mac_info):
                         G.add_node(target_mac_info)
                         print(f"[+] Nuevo nodo (desde target_mac_info) añadido: {target_mac_info}")
                         recompute = True

                    if cmd_type == "JOIN":
                        if not G.has_edge(mac_origin, 'ALL'):
                            G.add_edge(mac_origin, 'ALL')
                            edge_colors[tuple(sorted((mac_origin, 'ALL')))] = 'gray'
                            print(f"[*] Nodo {mac_origin} se unió (conectado a ALL).")
                            recompute = True
                    elif cmd_type == "BROADCAST_RECV" and target_mac_info:
                        # mac_origin es el nodo que recibió el broadcast ESP-NOW y está reportando vía UDP.
                        # target_mac_info es el nodo que originalmente emitió el broadcast ESP-NOW.
                        print(f"[*] Nodo {mac_origin} recibió broadcast ESP-NOW de {target_mac_info} (Payload: {cmd_payload})")
                        # Conectar mac_origin a 'ALL' (ya que participó en un broadcast)
                        if not G.has_edge(mac_origin, 'ALL'):
                            G.add_edge(mac_origin, 'ALL')
                            edge_colors[tuple(sorted((mac_origin, 'ALL')))] = 'gray'
                            recompute = True
                        # Añadir arista de vecindad entre el receptor y el emisor del broadcast ESP-NOW
                        if mac_origin != target_mac_info: # Evitar auto-bucles
                            edge = tuple(sorted((mac_origin, target_mac_info)))
                            if not G.has_edge(edge[0], edge[1]):
                                G.add_edge(edge[0], edge[1])
                                edge_colors[edge] = 'lightsteelblue' # Color para aristas descubiertas
                                print(f"[*] Arista de vecindad (por broadcast ESP-NOW) añadida: {edge[0]} <-> {edge[1]}")
                                recompute = True
                    elif cmd_type == "SEND_FAIL_TO" and target_mac_info:
                        print(f"[!] {mac_origin} reporta FALLO ESP-NOW a {target_mac_info}")
                        if not G.has_node(target_mac_info): G.add_node(target_mac_info)
                        remove_edge_or_mark_failed(mac_origin, target_mac_info, failed=True)
                    elif cmd_type == "UNICAST_RECV" and target_mac_info:
                        # mac_origin es el que recibió el UNICAST ESP-NOW.
                        # target_mac_info es el que envió el UNICAST ESP-NOW.
                        print(f"[<] {mac_origin} reporta UNICAST_RECV de {target_mac_info}")
                        edge_key = tuple(sorted((mac_origin, target_mac_info)))
                        if not G.has_edge(mac_origin, target_mac_info):
                            G.add_edge(mac_origin, target_mac_info)
                            edge_colors[edge_key] = 'blue' # Arista por unicast exitoso
                            recompute = True
                continue

            # Manejo de JOIN simple (si aún se usa, aunque el CMD:JOIN es más robusto)
            if line.startswith("JOIN "):
                parts = line.split()
                if len(parts) > 1:
                    mac = parts[1]
                    mac_ip[mac] = addr
                    with lock:
                        if not G.has_node(mac):
                            G.add_node(mac); recompute = True
                            print(f"[+] Nuevo nodo (JOIN simple): {mac}")
                        if not G.has_edge(mac,'ALL'):
                            G.add_edge(mac,'ALL')
                            edge_colors[tuple(sorted((mac,'ALL')))] = 'gray'
                            print(f"[*] Nodo {mac} se unió (JOIN simple, conectado a ALL).")
                            recompute = True
        except UnicodeDecodeError:
            print(f"[!] Error de decodificación UDP de {addr}")
        except ConnectionResetError:
            print(f"[!] Conexión reseteada por el peer: {addr}")
        except Exception as e:
            print(f"[!] Excepción en listener: {e} (tipo: {type(e)}) (linea: {line if 'line' in locals() else 'N/A'})")


threading.Thread(target=listener, daemon=True).start()

def broadcaster():
    # Este broadcaster es para que los ESPs sepan la IP del servidor Python
    # y puedan enviar sus mensajes JOIN o de estado.
    print("[i] Enviando broadcast ping UDP para descubrimiento del servidor...")
    sock.sendto(b'BROADCAST ping_servidor_python\n', ('255.255.255.255', UDP_PORT))
    threading.Timer(15, broadcaster).start() # Aumentado a 15 segundos para reducir spam
broadcaster() # Iniciar el broadcaster

# --- Configuración de la Figura y Axes (FUERA de update) ---
fig = plt.figure(figsize=(13, 9))
ax_main = fig.add_axes([0.05, 0.1, 0.9, 0.85])
ax_main.set_axis_off()

ax_dfs_button = fig.add_axes([0.30, 0.01, 0.18, 0.05])
dfs_button = Button(ax_dfs_button, 'DFS Route Send')
dfs_button.on_clicked(on_dfs_button_clicked)
dfs_button.ax.set_visible(False)

ax_bfs_button = fig.add_axes([0.52, 0.01, 0.18, 0.05])
bfs_button = Button(ax_bfs_button, 'BFS Route Send')
bfs_button.on_clicked(on_bfs_button_clicked)
bfs_button.ax.set_visible(False)

fig.canvas.mpl_connect('button_press_event', on_click)


def update(frame):
    global pos, recompute, G
    ax_main.cla()
    ax_main.set_axis_off()
    with lock:
        current_G = G.copy()
        if len(selected) == 2:
            dfs_button.ax.set_visible(True)
            bfs_button.ax.set_visible(True)
        else:
            dfs_button.ax.set_visible(False)
            bfs_button.ax.set_visible(False)

        if current_G.number_of_nodes() == 0:
            pos = {}
        elif recompute or not pos or not all(n in pos for n in current_G.nodes()) or \
             any(n not in current_G.nodes() for n in pos if len(pos) != len(current_G.nodes())):
            try:
                if len(current_G.nodes()) == 1:
                    pos = {list(current_G.nodes())[0]: (0.5,0.5)}
                else:
                    seed_pos = {k: v for k, v in pos.items() if k in current_G.nodes()}
                    if not seed_pos or len(seed_pos) < len(current_G.nodes()) / 2 : seed_pos = None
                    if current_G.number_of_nodes() > 1:
                        pos = nx.kamada_kawai_layout(current_G, pos=seed_pos, dim=2)
                    elif seed_pos:
                        pos = seed_pos
                    else:
                        pos = {list(current_G.nodes())[0]: (0.5,0.5)}
                print("[i] Recalculando layout.")
            except Exception as e_layout:
                print(f"[!] Error en layout Kamada-Kawai ({e_layout}), usando Spring.")
                try:
                    pos = nx.spring_layout(current_G, k=0.5, iterations=50, pos=pos if pos and all(n in current_G.nodes() for n in pos) else None)
                except Exception as e_spring:
                    print(f"[!] Error en layout Spring ({e_spring}), intentando layout aleatorio.")
                    pos = nx.random_layout(current_G)
            recompute = False

        node_colors_list = []
        for node_id in current_G.nodes():
            if node_id == 'ALL': node_colors_list.append('lightgreen')
            elif node_id in selected: node_colors_list.append('yellow')
            else: node_colors_list.append('skyblue')

        if current_G.number_of_nodes() > 0 and pos and len(pos) == current_G.number_of_nodes():
            nx.draw_networkx_nodes(current_G, pos, ax=ax_main, node_color=node_colors_list, node_size=700, alpha=0.9)
            edges_to_draw = list(current_G.edges())
            edge_color_list_to_draw = []
            edge_width_list_to_draw = []
            for u_orig, v_orig in edges_to_draw:
                edge_key = tuple(sorted((u_orig, v_orig)))
                current_edge_color = 'gray'
                current_edge_width = 2
                is_active_route_edge = False
                active_route_color_found = None
                for msg_id_iter, route_data in active_routes_viz.items(): # Renombrar msg_id
                    if isinstance(route_data, dict) and 'path' in route_data:
                        if edge_key in route_data['path']:
                            active_route_color_found = route_data.get('color', 'purple')
                            is_active_route_edge = True
                            break
                if is_active_route_edge:
                    current_edge_color = active_route_color_found
                    current_edge_width = 3.5 if current_edge_color in ['purple', 'lime', 'maroon', 'cyan'] else 2.5
                elif edge_key in edge_colors:
                    current_edge_color = edge_colors[edge_key]
                edge_color_list_to_draw.append(current_edge_color)
                edge_width_list_to_draw.append(current_edge_width)
            nx.draw_networkx_edges(current_G, pos, ax=ax_main, edgelist=edges_to_draw, edge_color=edge_color_list_to_draw, width=edge_width_list_to_draw, alpha=0.7)
            nx.draw_networkx_labels(current_G, pos, ax=ax_main, font_size=8, font_weight='bold')

    title_str = f"Red ESP-NOW — Nodos: {len([n for n in current_G.nodes() if n!='ALL'])}"
    if selected: title_str += f" (Seleccionados: {len(selected)})"
    active_route_count = sum(1 for r_data in active_routes_viz.values() if isinstance(r_data, dict) and r_data.get('status') not in ['delivered', 'failed_no_ip', 'timeout'])
    if active_route_count > 0:
        title_str += f" Rutas activas: {active_route_count}"
    ax_main.set_title(title_str)

ani = FuncAnimation(fig, update, interval=500, cache_frame_data=False)
plt.show()