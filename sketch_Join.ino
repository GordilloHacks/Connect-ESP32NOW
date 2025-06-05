//funcional join
#include <WiFi.h>
#include <esp_now.h>
#include <WiFiUdp.h>

#define LED_PIN   2  // LED integrado para envío general
#define LED2_PIN  4  // D4 para confirmación de recepción UNICAST y actividad de RUTA

const char* WIFI_SSID = "ActivoSo"; // Reemplaza con tu SSID
const char* WIFI_PASS = "87654321"; // Reemplaza con tu contraseña

WiFiUDP udp;
const uint16_t UDP_PORT = 12345;
IPAddress bcast(255,255,255,255); // Para enviar UDP al script Python si su IP no es conocida
// Opcionalmente, puedes definir la IP del servidor Python si es fija:
// IPAddress serverIP(192, 168, 1, 100); // Cambia esto a la IP de tu PC ejecutando Python

typedef struct {
  char command[16]; 
  char text[128]; // Aumentado para payload de ruta + tipo de conexión
} msg_t;

uint8_t broadcastAddress[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

// Variables globales para el estado del LED de ruta
bool led2_route_active = false;
char current_route_msg_id_for_led[12] = {0}; 

void blinkLed(int pin, int times, int on_ms, int off_ms = -1) {
  if (off_ms == -1) off_ms = on_ms;
  for (int i = 0; i < times; i++) {
    digitalWrite(pin, HIGH); delay(on_ms);
    digitalWrite(pin, LOW);  delay(off_ms);
  }
}

bool macStringToBytes(const char* macStr, uint8_t* bytes) {
  return sscanf(macStr, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx", 
                &bytes[0], &bytes[1], &bytes[2], &bytes[3], &bytes[4], &bytes[5]) == 6;
}

void sendUDP(const String &s) {
  // Intentar enviar a la IP específica del servidor si está definida y es válida
  // if (serverIP[0] != 0) { // Asumiendo que 0.0.0.0 no es una IP válida para el servidor
  //   udp.beginPacket(serverIP, UDP_PORT);
  // } else {
     udp.beginPacket(bcast, UDP_PORT); // Fallback a broadcast si no hay IP de servidor
  // }
  udp.print(s);
  udp.endPacket();
  Serial.print("UDP Sent: "); Serial.println(s);
}

void ensurePeer(const uint8_t* mac_addr) {
  if (esp_now_is_peer_exist(mac_addr)) return;
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, mac_addr, 6);
  peer.channel = 0; // 0 para usar el canal WiFi actual
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK){
    Serial.println("Fallo al añadir peer ESP-NOW");
  } else {
    char macStr[18];
    sprintf(macStr, "%02X:%02X:%02X:%02X:%02X:%02X", mac_addr[0], mac_addr[1], mac_addr[2], mac_addr[3], mac_addr[4], mac_addr[5]);
    Serial.printf("Peer ESP-NOW añadido: %s\n", macStr);
  }
}

void onSent(const uint8_t* mac_addr, esp_now_send_status_t status) {
  char macStr[18];
  sprintf(macStr, "%02X:%02X:%02X:%02X:%02X:%02X", mac_addr[0], mac_addr[1], mac_addr[2], mac_addr[3], mac_addr[4], mac_addr[5]);
  Serial.printf("Estado envío ESP-NOW a %s: %s\n", macStr, status == ESP_NOW_SEND_SUCCESS ? "Éxito" : "Fallo");

  if (status == ESP_NOW_SEND_SUCCESS) {
    blinkLed(LED_PIN, 1, 50);
  } else {
    blinkLed(LED_PIN, 3, 100);
    String failMsg = "<" + WiFi.macAddress() + "> CMD:SEND_FAIL_TO " + String(macStr);
    sendUDP(failMsg);
  }
}

void onRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  msg_t m = {}; 
  if (len > 0 && len <= sizeof(m)) { 
    memcpy(&m, data, len);
    // Asegurar terminación null para m.text si se va a usar como C-string directamente
    // El offset de text dentro de msg_t es sizeof(m.command)
    // Si len cubre parte o todo m.text, necesitamos asegurar el null termination
    int command_len_actual = 0;
    for(int i=0; i<sizeof(m.command); ++i){ if(m.command[i]=='\0'){ break; } command_len_actual++;}

    int text_offset_in_struct = sizeof(m.command); // Asumiendo que m.command está al inicio
    int text_len_received = len - text_offset_in_struct;

    if (text_len_received > 0 && text_len_received < sizeof(m.text)) {
        m.text[text_len_received] = '\0';
    } else if (text_len_received >= sizeof(m.text)-1) { // Si text_len_received es igual o mayor al buffer de texto
        m.text[sizeof(m.text)-1] = '\0'; // Truncar y asegurar null
    } else { // No text part or invalid
        m.text[0] = '\0'; // Empty text
    }


  } else if (len > sizeof(m)) {
    Serial.println("Error: Paquete ESP-NOW recibido demasiado largo.");
    return;
  } else { 
    Serial.println("Error: Paquete ESP-NOW recibido con longitud inválida o cero.");
    return;
  }

  char srcMacStr[18];
  sprintf(srcMacStr, "%02X:%02X:%02X:%02X:%02X:%02X", info->src_addr[0], info->src_addr[1], info->src_addr[2], info->src_addr[3], info->src_addr[4], info->src_addr[5]);
  ensurePeer(info->src_addr); // Asegurar que el remitente es un peer para futuras respuestas

  Serial.printf("ESP-NOW Recibido de %s. Comando: '%s', Texto: '%s'\n", srcMacStr, m.command, m.text);
  
  String myMac = WiFi.macAddress();
  String cmdTypeStr = String(m.command);
  cmdTypeStr.toUpperCase(); // Normalizar comando a mayúsculas

  // Informar al servidor Python sobre la recepción de este mensaje ESP-NOW
  // El formato es <MI_MAC_QUE_RECIBIO_ESPNOW> CMD:<COMANDO_ESPNOW_RECIBIDO>_RECV <MAC_QUE_ENVIO_ESPNOW> <TEXTO_DEL_ESPNOW>
  sendUDP("<" + myMac + "> CMD:" + cmdTypeStr + "_RECV " + String(srcMacStr) + " " + String(m.text));

  if (strcmp(m.command, "UNICAST") == 0) {
    Serial.println("Comando UNICAST recibido vía ESP-NOW. Confirmando...");
    // Adicionalmente, el script de Python podría querer un ACK más específico
    // sendUDP("RECEIVED " + myMac + " " + String(srcMacStr)); // Esto es un poco redundante con el _RECV de arriba
    blinkLed(LED2_PIN, 7, 100, 50);
  } else if (strcmp(m.command, "BROADCAST") == 0) {
    Serial.println("Comando BROADCAST recibido vía ESP-NOW.");
    blinkLed(LED2_PIN, 1, 200);
  } else if (strcmp(m.command, "JOIN_ESPNOW") == 0) { // Manejo del JOIN_ESPNOW
    Serial.println("Comando JOIN_ESPNOW recibido vía ESP-NOW.");
    // El sendUDP genérico de arriba ya informa al servidor.
    // El texto de m.text en un JOIN_ESPNOW suele ser la MAC del que envió el JOIN.
    blinkLed(LED_PIN, 1, 50); 
    blinkLed(LED2_PIN,1, 50);
  }
  else if (strcmp(m.command, "ROUTED_DATA") == 0) {
    Serial.println("Comando ROUTED_DATA recibido vía ESP-NOW.");
    
    char final_dest_mac_in_msg[18] = {0};
    char msg_id_in_msg[12] = {0}; 
    char actual_payload_with_type[96] = {0}; // Ajustar tamaño si es necesario

    // Parsear m.text: "<final_dest_mac> <msg_id> <payload_con_tipo>"
    int parsed_items = sscanf(m.text, "%17s %11s %95[^\n]", final_dest_mac_in_msg, msg_id_in_msg, actual_payload_with_type);

    if (parsed_items >= 3) { 
      Serial.printf("  Ruta ESP-NOW - Destino Final en Msg: %s, MSG_ID: %s, Payload: %s\n", final_dest_mac_in_msg, msg_id_in_msg, actual_payload_with_type);
      
      if (myMac.equalsIgnoreCase(final_dest_mac_in_msg)) {
        Serial.println("  >> ESTE ESP ES EL DESTINO FINAL DE LA RUTA! <<");
        // Informar al servidor Python que el mensaje ha sido entregado
        sendUDP("ROUTE_DELIVERED " + myMac + " " + String(srcMacStr) + " " + String(msg_id_in_msg) + " " + String(actual_payload_with_type));
        blinkLed(LED_PIN, 5, 80); 
        blinkLed(LED2_PIN, 5, 80, 80); 
        
        if (led2_route_active && strcmp(msg_id_in_msg, current_route_msg_id_for_led) == 0) {
          led2_route_active = false; 
          current_route_msg_id_for_led[0] = '\0';
          digitalWrite(LED2_PIN, LOW); // Apagar LED de ruta si estaba encendido por ser intermediario antes
        }

      } else {
        Serial.println("  Este ESP NO es el destino final del ROUTED_DATA (fue solo un receptor ESP-NOW).");
        // Un nodo intermedio no debería recibir ROUTED_DATA directamente si no fue por un ROUTE_STEP UDP.
        // Si lo recibe, simplemente lo ignora para reenvío (Python maneja el reenvío).
      }
    } else {
      Serial.printf("Error parseando el texto de ROUTED_DATA. Items parseados: %d. Texto: '%s'\n", parsed_items, m.text);
    }
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LOW);
  pinMode(LED2_PIN, OUTPUT); digitalWrite(LED2_PIN, LOW);
  Serial.begin(115200);
  delay(1000); 

  Serial.println("\n\n===========================");
  Serial.println("Iniciando ESP32...");
  Serial.printf("Modelo Chip: %s Rev: %d\n", ESP.getChipModel(), ESP.getChipRevision());
  Serial.printf("Memoria Heap Libre (inicio setup): %u bytes\n", ESP.getFreeHeap());

  WiFi.mode(WIFI_STA);
  // WiFi.setSleep(false); // Puede ayudar con estabilidad de ESP-NOW a costa de consumo

  Serial.printf("Intentando conectar a SSID: '%s'\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long wifiStartTime = millis();
  const unsigned long WIFI_CONNECT_TIMEOUT_MS = 15000; 

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - wifiStartTime > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("\nTimeout conectando a WiFi. Reiniciando ESP...");
      delay(1000);
      ESP.restart();
    }
  }

  Serial.println("\n¡WiFi Conectado!");
  Serial.print("Dirección IP: "); Serial.println(WiFi.localIP());
  Serial.print("Dirección MAC: "); Serial.println(WiFi.macAddress());
  Serial.printf("Memoria Heap Libre (después de WiFi): %u bytes\n", ESP.getFreeHeap());

  Serial.println("Inicializando UDP...");
  if (udp.begin(UDP_PORT)) {
    Serial.printf("Socket UDP escuchando en puerto %d\n", UDP_PORT);
  } else {
    Serial.println("¡FALLO al iniciar UDP!");
  }
  Serial.printf("Memoria Heap Libre (después de UDP): %u bytes\n", ESP.getFreeHeap());

  Serial.println("Inicializando ESP-NOW...");
  if (esp_now_init() != ESP_OK) {
    Serial.println("¡ERROR inicializando ESP-NOW! Reiniciando.");
    ESP.restart();
    return; 
  }
  Serial.println("ESP-NOW inicializado.");
  esp_now_register_send_cb(onSent);
  esp_now_register_recv_cb(onRecv);
  ensurePeer(broadcastAddress); // Añadir peer para broadcasts ESP-NOW
  Serial.printf("Memoria Heap Libre (después de ESP-NOW): %u bytes\n", ESP.getFreeHeap());

  Serial.println("Enviando mensajes de JOIN iniciales...");
  // 1. JOIN UDP al servidor Python
  sendUDP("<" + WiFi.macAddress() + "> CMD:JOIN");
  
  // 2. JOIN_ESPNOW broadcast a otros ESPs
  msg_t jm_espnow = {}; 
  strcpy(jm_espnow.command, "JOIN_ESPNOW");
  // El payload del JOIN_ESPNOW es la propia MAC del que lo envía, para que otros sepan quién es
  strncpy(jm_espnow.text, WiFi.macAddress().c_str(), sizeof(jm_espnow.text) - 1);
  jm_espnow.text[sizeof(jm_espnow.text) - 1] = '\0'; 
  
  esp_err_t join_send_result = esp_now_send(broadcastAddress, (uint8_t*)&jm_espnow, sizeof(jm_espnow));
  if (join_send_result == ESP_OK) {
    Serial.println("Mensaje JOIN_ESPNOW encolado para envío broadcast.");
  } else {
    Serial.printf("Fallo al encolar JOIN_ESPNOW. Código de error: %d\n", join_send_result);
  }
  
  blinkLed(LED_PIN, 2, 100); 
  Serial.println("Setup completado.");
  Serial.printf("Memoria Heap Libre (final de setup): %u bytes\n", ESP.getFreeHeap());
  Serial.println("===========================");
}

void loop() {
  int packetSize = udp.parsePacket();
  if (packetSize) {
    char incomingPacket[256]; 
    int len = udp.read(incomingPacket, sizeof(incomingPacket) - 1);
    if (len > 0) {
        incomingPacket[len] = 0; 
    } else {
        Serial.println("Error leyendo paquete UDP o paquete vacío.");
        return; 
    }
    
    String cmd = String(incomingPacket); cmd.trim();
    Serial.print("UDP Recibido: "); Serial.println(cmd);
    String myMac = WiFi.macAddress();

    if (cmd.startsWith("BROADCAST ")) {
      String txt = cmd.substring(cmd.indexOf(' ') + 1);
      msg_t m_bcast = {}; 
      strcpy(m_bcast.command, "BROADCAST");
      strncpy(m_bcast.text, txt.c_str(), sizeof(m_bcast.text)-1); m_bcast.text[sizeof(m_bcast.text)-1] = '\0';
      ensurePeer(broadcastAddress); // Asegurar que el peer broadcast existe
      esp_now_send(broadcastAddress, (uint8_t*)&m_bcast, sizeof(m_bcast));
    } else if (cmd.startsWith("UNICAST ")) {
      int firstSpace = cmd.indexOf(' ');
      int secondSpace = cmd.indexOf(' ', firstSpace + 1);
      if (firstSpace > 0 && secondSpace > firstSpace) {
        String tgtMacStr = cmd.substring(firstSpace + 1, secondSpace);
        String txt = cmd.substring(secondSpace + 1);
        uint8_t destMac[6];
        if (macStringToBytes(tgtMacStr.c_str(), destMac)) {
          ensurePeer(destMac);
          msg_t m_ucast = {}; 
          strcpy(m_ucast.command, "UNICAST");
          strncpy(m_ucast.text, txt.c_str(), sizeof(m_ucast.text)-1); m_ucast.text[sizeof(m_ucast.text)-1] = '\0';
          esp_now_send(destMac, (uint8_t*)&m_ucast, sizeof(m_ucast));
        } else { Serial.println("Error: Formato MAC incorrecto en UNICAST UDP."); }
      } else { Serial.println("Error: Formato comando UNICAST UDP incorrecto."); }
    } 
    else if (cmd.startsWith("ROUTE_STEP ")) { 
      Serial.println("Procesando comando UDP: ROUTE_STEP");
      
      char next_hop_mac_str[18] = {0};
      char final_dest_mac_str[18] = {0};
      char msg_id_str[12] = {0};       
      char route_payload_with_type[96] = {0}; 

      int parsed = sscanf(cmd.c_str(), "ROUTE_STEP %17s %17s %11s %95[^\n]", 
                                      next_hop_mac_str, final_dest_mac_str, msg_id_str, route_payload_with_type);

      if (parsed >= 4) { 
        Serial.printf("  UDP R_S - Next Hop: %s, Final Dest: %s, MSG_ID: %s, Payload: %s\n",
                      next_hop_mac_str, final_dest_mac_str, msg_id_str, route_payload_with_type);
        
        sendUDP("ACK_ROUTE_STEP_RECEIVED " + myMac + " " + String(msg_id_str));

        // Si este ESP no es el destino final de la ruta Y es un nodo intermedio para este msg_id
        if (!myMac.equalsIgnoreCase(final_dest_mac_str)) {
          Serial.printf("  Soy nodo intermedio para ruta %s. Encendiendo LED D4 (ruta).\n", msg_id_str);
          digitalWrite(LED2_PIN, HIGH);
          led2_route_active = true;
          strncpy(current_route_msg_id_for_led, msg_id_str, sizeof(current_route_msg_id_for_led)-1);
          current_route_msg_id_for_led[sizeof(current_route_msg_id_for_led)-1] = '\0';
        } else {
            // Si soy el destino final según ROUTE_STEP, esto es un poco extraño,
            // ya que el destino final normalmente reacciona al ROUTED_DATA de ESP-NOW.
            // No se enciende el LED de "intermediario" aquí.
            Serial.println("  Soy el destino final según esta orden ROUTE_STEP (esto es para el último salto).");
        }

        uint8_t next_hop_bytes[6];
        if (macStringToBytes(next_hop_mac_str, next_hop_bytes)) {
          ensurePeer(next_hop_bytes);
          
          msg_t m_route_espnow = {};
          strcpy(m_route_espnow.command, "ROUTED_DATA");
          // El payload para ESP-NOW es: "<final_dest_mac_str> <msg_id_str> <route_payload_with_type>"
          snprintf(m_route_espnow.text, sizeof(m_route_espnow.text), "%s %s %s", 
                   final_dest_mac_str, msg_id_str, route_payload_with_type); 

          esp_err_t result = esp_now_send(next_hop_bytes, (uint8_t*)&m_route_espnow, sizeof(m_route_espnow));
          if (result == ESP_OK) {
            // Informar al servidor Python que el envío ESP-NOW de este salto fue encolado
            sendUDP("ACK_ROUTE_ESPNOW_SENT " + myMac + " " + String(next_hop_mac_str) + " " + String(msg_id_str));
          } else {
            sendUDP("FAIL_ROUTE_ESPNOW_SENT " + myMac + " " + String(next_hop_mac_str) + " " + String(msg_id_str) + " ERR:" + String(result));
          }
        } else { Serial.println("Error: Formato MAC incorrecto para next_hop en ROUTE_STEP."); }
      } else { Serial.printf("Error: Formato de comando ROUTE_STEP incorrecto. Items parseados: %d. Comando: '%s'\n", parsed, cmd.c_str()); }
    } else if (cmd.startsWith("ROUTE_MSG_ACKNOWLEDGED ")) { 
        char ack_msg_id_str[12] = {0};
        if (sscanf(cmd.c_str(), "ROUTE_MSG_ACKNOWLEDGED %11s", ack_msg_id_str) == 1) {
            if (led2_route_active && strcmp(ack_msg_id_str, current_route_msg_id_for_led) == 0) {
                Serial.printf("  Ruta %s completada globalmente. Apagando LED D4 (ruta).\n", ack_msg_id_str);
                digitalWrite(LED2_PIN, LOW);
                led2_route_active = false;
                current_route_msg_id_for_led[0] = '\0';
            } else {
                Serial.printf("  Recibido ROUTE_MSG_ACKNOWLEDGED para %s, pero no coincide con LED activo (%s) o LED no activo.\n", ack_msg_id_str, current_route_msg_id_for_led);
            }
        } else {
            Serial.printf("Error parseando ROUTE_MSG_ACKNOWLEDGED. Comando: '%s'\n", cmd.c_str());
        }
    }
  }
  delay(10); 
}