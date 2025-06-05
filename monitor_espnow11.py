# --- monitor_espnow_sim_blend (Revisado v8) ---

import socket, re, threading, time, uuid
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import numpy as np

# --- UDP ---
UDP_PORT = 12345
pattern_cmd = re.compile(r'<([0-9A-F:]{17})>\s+CMD:(\w+)(?:\s+([0-9A-F:]{17}))?(?:\s+(.*))?')
pattern_route_delivered = re.compile(r'ROUTE_DELIVERED\s+([0-9A-F:]{17})\s+([0-9A-F:]{17})\s+([\w-]+)\s+(.*)')
pattern_ack_route_step_rcvd = re.compile(r'ACK_ROUTE_STEP_RECEIVED\s+([0-9A-F:]{17})\s+([\w-]+)')
pattern_ack_route_espnow_sent = re.compile(r'ACK_ROUTE_ESPNOW_SENT\s+([0-9A-F:]{17})\s+([0-9A-F:]{17})\s+([\w-]+)')

# --- Colores y Estilos ---
COLOR_NODE_DEFAULT = 'skyblue'
COLOR_NODE_ALL = 'lightgreen'
COLOR_NODE_SELECTED = 'yellow'
COLOR_NODE_TEMP_HIGHLIGHT = 'orange'
COLOR_EDGE_BASE = 'lightgray'      
COLOR_EDGE_ESTABLISHED = 'lightblue'
COLOR_EDGE_ATTEMPT = 'black'       
COLOR_EDGE_VIA_ESTABLISHED = 'green'
COLOR_EDGE_FAIL = 'red'            
COLOR_EDGE_TEMP_SUCCESS = 'lime'   
STYLE_THIN = 1.5; STYLE_NORMAL = 2.0; STYLE_THICK = 3.0
TEMPORARY_VISUALIZATION_SECONDS = 5.0
NODE_CLICK_RADIUS_SQ = 0.0025 

# --- Grafo y estado ---
G = nx.Graph()
lock = threading.Lock()
mac_ip = {}; edge_temp_visuals = {}; pos = {}
recompute_layout = True; selected_nodes = []; active_communications = {}
G.add_node('ALL'); pos['ALL'] = (0.5, 0.1) 

# --- Socket UDP ---
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.bind(('', UDP_PORT))

def get_edge_key(u, v): return tuple(sorted((u, v)))

def set_edge_status_in_G(u, v, status_str): # Sin cambios
    global recompute_layout
    edge_key = get_edge_key(u,v)
    with lock:
        nodes_added = False
        if u not in G: G.add_node(u); nodes_added = True
        if v not in G: G.add_node(v); nodes_added = True
        if nodes_added: recompute_layout = True
        edge_structural_change = False
        if not G.has_edge(u,v): G.add_edge(u,v, status=status_str); edge_structural_change=True
        elif G.edges[edge_key].get('status') != status_str: G.edges[edge_key]['status'] = status_str
        if edge_structural_change and not nodes_added: recompute_layout = True

def apply_temp_visual(ek_list, color, width=STYLE_THICK, duration=TEMPORARY_VISUALIZATION_SECONDS): # Sin cambios
    global edge_temp_visuals
    # ek_list DEBE ser una lista de tuplas de aristas, ej: [('N1','N2'), ('N2','N3')]
    # O una sola tupla de arista, ej: ('N1','N2'), que se convertirá en lista.
    if not isinstance(ek_list, list): 
        ek_list = [ek_list] # Si se pasa una sola tupla ('N1','N2'), se convierte en [('N1','N2')]
    
    exp_time = time.time() + duration
    with lock:
        for u_node,v_node in ek_list: # Ahora u_node y v_node se desempaquetan correctamente de cada tupla en ek_list
            edge_temp_visuals[get_edge_key(u_node,v_node)] = {'color':color,'width':width,'expires':exp_time}

def clear_expired_temp_visuals(): # Sin cambios
    global edge_temp_visuals; now=time.time(); changed=False
    with lock:
        keys_del=[k for k,v in edge_temp_visuals.items() if v['expires']<=now]
        for k in keys_del: del edge_temp_visuals[k]; changed=True
    return changed 

def on_click(event): # Sin cambios
    global recompute_layout, selected_nodes, pos
    if event.inaxes != ax_main or event.xdata is None or event.ydata is None: return
    clicked_node = None; min_d_sq = NODE_CLICK_RADIUS_SQ
    with lock: current_p_copy = dict(pos); g_nodes_copy = list(G.nodes())
    for n in g_nodes_copy:
        if n in current_p_copy:
            xn,yn=current_p_copy[n]
            if abs(xn-event.xdata)<NODE_CLICK_RADIUS_SQ**0.5 and abs(yn-event.ydata)<NODE_CLICK_RADIUS_SQ**0.5:
                d_sq=(xn-event.xdata)**2+(yn-event.ydata)**2
                if d_sq<min_d_sq: clicked_node=n; min_d_sq=d_sq
    if clicked_node:
        action=False
        with lock:
            if clicked_node not in selected_nodes:
                if len(selected_nodes)<2: selected_nodes.append(clicked_node); action=True
            else: selected_nodes.remove(clicked_node); action=True
            if len(selected_nodes)==2:
                s_nodes=list(selected_nodes);selected_nodes.clear();recompute_layout=True
                threading.Thread(target=initiate_simulation_interaction,args=(s_nodes[0],s_nodes[1]),daemon=True).start()
                action=True
            elif action: recompute_layout=True
        s_msg = f"Seleccionados: {selected_nodes}" if selected_nodes else "Seleccione nodo"
        if clicked_node and not action and len(selected_nodes)==2: s_msg = f"Ya hay 2 seleccionados: {selected_nodes}"
        elif clicked_node and action: s_msg = f"{'Seleccionado' if clicked_node in selected_nodes else 'Deseleccionado'} {clicked_node}. {s_msg}"
        update_figure_title(s_msg)

def initiate_simulation_interaction(s1, s2): # Sin cambios
    global active_communications, mac_ip
    msg_id=str(uuid.uuid4())[:8]; payload=f"data_{msg_id[:4]}"
    update_figure_title(f"Procesando {s1}->{s2}...")
    G_est=nx.Graph(); path_via_est=None
    with lock:
        for u,v,d in G.edges(data=True):
            if d.get('status')=='established': G_est.add_edge(u,v)
    if G_est.has_node(s1) and G_est.has_node(s2):
        try: path_via_est=nx.shortest_path(G_est,source=s1,target=s2)
        except (nx.NetworkXNoPath,nx.NodeNotFound):pass
    if path_via_est and len(path_via_est)>1:
        p_edges=[(path_via_est[i],path_via_est[i+1]) for i in range(len(path_via_est)-1)]
        with lock:active_communications[msg_id]={'type':'route_via_established','path_edges':[get_edge_key(u,v) for u,v in p_edges],'path_nodes':path_via_est,'status':'pending'}
        apply_temp_visual(p_edges,COLOR_EDGE_VIA_ESTABLISHED,STYLE_THICK) # p_edges ya es una lista de tuplas
        for i in range(len(path_via_est)-1):
            snd,rcv,fdest=path_via_est[i],path_via_est[i+1],path_via_est[-1]
            if snd not in mac_ip:print(f"[!]No IP:{snd}");return
            sock.sendto(f"ROUTE_STEP {rcv} {fdest} {msg_id} {payload}\n".encode(),mac_ip[snd]);time.sleep(0.1)
        update_figure_title(f"Ruta Verde {s1}->{s2}")
    elif s1!=s2:
        ek_dir=get_edge_key(s1,s2) # ek_dir es una tupla
        with lock:active_communications[msg_id]={'type':'direct_attempt','path_edges':[ek_dir],'path_nodes':[s1,s2],'status':'pending'}
        apply_temp_visual([(s1,s2)],COLOR_EDGE_ATTEMPT,STYLE_NORMAL) # Se pasa una lista conteniendo la tupla de arista
        if s1 not in mac_ip:
            set_edge_status_in_G(s1,s2,'failed');apply_temp_visual([(s1,s2)],COLOR_EDGE_FAIL,duration=0.2)
            with lock:
                if msg_id in active_communications:active_communications[msg_id]['status']='failed_no_ip'
            update_figure_title(f"FALLO IP {s1}");return
        sock.sendto(f"UNICAST {s2} {payload}\n".encode(),mac_ip[s1])
        update_figure_title(f"Intento UNICAST {s1}->{s2}")
    else:update_figure_title("Origen y destino iguales.")

def listener(): # Cambio en el manejo de ROUTE_DELIVERED
    global recompute_layout, G, mac_ip, active_communications
    while True:
        try:
            data,addr=sock.recvfrom(1024);line=data.decode().strip(); # print(f"[UDP_RX {addr}]{line}") # Comentado para reducir log spam si funciona
            m_cmd=pattern_cmd.search(line)
            if m_cmd:
                mac_o,cmd_t,mac_t,_=m_cmd.groups();mac_ip[mac_o]=addr
                with lock:
                    added=False
                    if mac_o not in G:G.add_node(mac_o);print(f"[+]Nodo:{mac_o}");added=True
                    if mac_t and mac_t not in G:G.add_node(mac_t);print(f"[+]Nodo(tgt):{mac_t}");added=True
                    if added:recompute_layout=True
                ek_inv=get_edge_key(mac_o,mac_t) if mac_t else None
                if cmd_t=="JOIN":set_edge_status_in_G(mac_o,'ALL','base')
                elif cmd_t=="BROADCAST_RECV" and ek_inv:
                    with lock:cur_stat=G.edges[ek_inv].get('status') if G.has_edge(*ek_inv) else None
                    if cur_stat!='established':set_edge_status_in_G(mac_o,mac_t,'base')
                elif cmd_t=="UNICAST_RECV" and ek_inv:
                    set_edge_status_in_G(mac_t,mac_o,'established');apply_temp_visual([ek_inv],COLOR_EDGE_TEMP_SUCCESS,STYLE_THICK,TEMPORARY_VISUALIZATION_SECONDS)
                    mid_res=None
                    with lock:
                        for mid,cdata in active_communications.items():
                            if cdata['type']=='direct_attempt' and cdata['path_edges'][0]==ek_inv and cdata['path_nodes'][0]==mac_t and cdata['path_nodes'][1]==mac_o:
                                cdata['status']='success';mid_res=mid;break
                    if mid_res:update_figure_title(f"UNICAST {mac_t[:5]}->{mac_o[:5]} OK!")
                elif cmd_t=="SEND_FAIL_TO" and ek_inv:
                    set_edge_status_in_G(mac_o,mac_t,'failed');apply_temp_visual([ek_inv],COLOR_EDGE_FAIL,STYLE_THICK,0.5)
                    update_figure_title(f"FALLO {mac_o[:5]}->{mac_t[:5]}")
                    with lock:
                        for mid,cdata in active_communications.items():
                            if ek_inv in cdata.get('path_edges',[]):
                                if(cdata['type']=='direct_attempt' and cdata['path_nodes'][0]==mac_o)or cdata['type']=='route_via_established':
                                    cdata['status']='failed';break
            m_ack=pattern_ack_route_espnow_sent.match(line)
            if m_ack:s,st,mid=m_ack.groups();apply_temp_visual(get_edge_key(s,st),COLOR_EDGE_TEMP_SUCCESS,STYLE_NORMAL,1.5) # get_edge_key devuelve tupla, se envuelve en lista por apply_temp_visual
            
            m_del=pattern_route_delivered.match(line)
            if m_del:fdest,_,mid,_=m_del.groups()
            if m_del and mid in active_communications and active_communications[mid]['type']=='route_via_established':
                C=active_communications[mid];C['status']='success'
                # C['path_edges'] es una lista de tuplas de aristas (edge_keys)
                # apply_temp_visual espera una lista de tuplas de aristas (u,v)
                # Como C['path_edges'] ya es una lista de estas tuplas, se puede pasar directamente.
                apply_temp_visual(C['path_edges'], COLOR_EDGE_TEMP_SUCCESS, STYLE_THICK, 2.0)
                for e_key in C['path_edges']: # e_key es una tupla (u,v)
                    set_edge_status_in_G(e_key[0], e_key[1], 'established') # Asegurar que todas las aristas de la ruta son azules
                update_figure_title(f"Ruta {mid[:4]} OK!")
        except Exception as e:print(f"[!]ExListener:{e}");import traceback;traceback.print_exc()

threading.Thread(target=listener,daemon=True).start()
def broadcaster():sock.sendto(b'BROADCAST ping_servidor_python\n',('255.255.255.255',UDP_PORT));threading.Timer(20,broadcaster).start()
# broadcaster()

fig=plt.figure(figsize=(13,9));ax_main=fig.add_axes([0.05,0.08,0.9,0.88]);ax_main.set_axis_off()
fig.canvas.mpl_connect('button_press_event',on_click);_figure_title_ax_main=ax_main

def update_figure_title(message_override=None): # Sin cambios
    ax=_figure_title_ax_main;num_n,sel_s,act_s=0,"",""
    with lock:
        num_n=len([n for n in G.nodes() if n!='ALL']);sel_c=list(selected_nodes)
        if sel_c:sel_s=f" (Seleccionados: {', '.join(sel_c)})"
        act_c=sum(1 for c in active_communications.values() if c.get('status','').startswith('pending'))
        if act_c>0:act_s=f" | Comms activas: {act_c}"
    title=message_override if message_override else f"Red ESP-NOW — Nodos: {num_n}{sel_s}{act_s}"
    ax.set_title(title,fontsize=10)

def update(frame): # Sin cambios mayores en la lógica de layout, solo limpieza
    global pos, recompute_layout
    if clear_expired_temp_visuals(): pass
        
    with lock:
        current_G_copy=G.copy();current_pos_copy=dict(pos);current_selected_copy=list(selected_nodes)
        current_temp_visuals_copy=dict(edge_temp_visuals);current_active_comms_copy=dict(active_communications)
    ax_main.cla();ax_main.set_axis_off();update_figure_title()
    nodes_in_G=list(current_G_copy.nodes())
    layout_needed=recompute_layout or not all(n in current_pos_copy for n in nodes_in_G) or \
                  (nodes_in_G and len(current_pos_copy)!=len(nodes_in_G))

    if not nodes_in_G:
        with lock:pos.clear();current_pos_copy.clear();recompute_layout=False
    elif layout_needed:
        try:
            layout_input_G=current_G_copy
            pos_arg_for_spring={k:v for k,v in current_pos_copy.items() if k in layout_input_G} if current_pos_copy else {}
            fixed_nodes_list=None
            if 'ALL' in layout_input_G:
                if 'ALL' not in pos_arg_for_spring:pos_arg_for_spring['ALL']=pos.get('ALL',(0.5,0.1))
                fixed_nodes_list=['ALL']
            if layout_input_G.number_of_nodes()==1:
                 node_s=list(layout_input_G.nodes())[0]
                 if node_s not in pos_arg_for_spring:pos_arg_for_spring[node_s]=(0.5,0.5)
                 new_positions=pos_arg_for_spring
            elif layout_input_G.number_of_nodes()>0:
                new_positions=nx.spring_layout(layout_input_G,pos=pos_arg_for_spring if pos_arg_for_spring else None,
                                               fixed=fixed_nodes_list,k=0.7,iterations=50,seed=7)
            else:new_positions={}
            with lock:pos.update(new_positions)
            recompute_layout=False;current_pos_copy=pos.copy()
        except Exception as e:print(f"[!]ErrorLayout:{e}");import traceback;traceback.print_exc()
    
    if not current_G_copy.nodes() or not current_pos_copy:fig.canvas.draw_idle();return
    drawable_nodes=[n for n in current_G_copy.nodes() if n in current_pos_copy]
    if not drawable_nodes:fig.canvas.draw_idle();return
    node_colors_list=[]
    for node_id in drawable_nodes:
        is_act_node=False
        with lock:is_act_node=any(node_id in c.get('path_nodes',[]) for c in active_communications.values() if c.get('status','').startswith('pending'))
        if node_id in current_selected_copy:node_colors_list.append(COLOR_NODE_SELECTED)
        elif is_act_node and node_id not in current_selected_copy:node_colors_list.append(COLOR_NODE_TEMP_HIGHLIGHT)
        elif node_id=='ALL':node_colors_list.append(COLOR_NODE_ALL)
        else:node_colors_list.append(COLOR_NODE_DEFAULT)
    nx.draw_networkx_nodes(current_G_copy,current_pos_copy,ax=ax_main,nodelist=drawable_nodes,node_color=node_colors_list,node_size=700,alpha=0.9,edgecolors='black',linewidths=1.0)
    labels_draw={n:str(n) for n in drawable_nodes}
    nx.draw_networkx_labels(current_G_copy,current_pos_copy,ax=ax_main,labels=labels_draw,font_size=8,font_weight='bold')
    for u,v,data in current_G_copy.edges(data=True):
        ek=get_edge_key(u,v)
        if not all(n in current_pos_copy for n in ek):continue
        stat=data.get('status','base');col,wid,alp=COLOR_EDGE_BASE,STYLE_NORMAL,0.5
        if stat=='established':col,wid,alp=COLOR_EDGE_ESTABLISHED,STYLE_NORMAL,0.8
        elif stat=='failed':col,wid,alp=COLOR_EDGE_FAIL,STYLE_NORMAL,0.7
        if ek in current_temp_visuals_copy:tmp=current_temp_visuals_copy[ek];col,wid,alp=tmp['color'],tmp['width'],0.95
        nx.draw_networkx_edges(current_G_copy,current_pos_copy,ax=ax_main,edgelist=[(u,v)],edge_color=col,width=wid,alpha=alp)
    fig.canvas.draw_idle()

ani=FuncAnimation(fig,update,interval=300,cache_frame_data=False)
plt.show()