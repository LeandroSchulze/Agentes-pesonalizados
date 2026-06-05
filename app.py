import os
import psycopg2
import openai
from flask import Flask, render_template, request, jsonify

# NUEVO: Importamos las funciones de nuestro procesador de documentos
from document_processor import process_and_store_document, get_embedding

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dbname")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# RUTA 1: Dashboard de Administración
@app.route('/')
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, specialty, status FROM agents ORDER BY id;")
    agents = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('dashboard.html', agents=agents)

# RUTA 2: Interfaz del Cliente (El Chat)
@app.route('/agent/<int:agent_id>')
def agent_chat(agent_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, specialty FROM agents WHERE id = %s", (agent_id,))
    agent = cur.fetchone()
    cur.close()
    conn.close()
    if not agent:
        return "Agente no encontrado", 404
    return render_template('chat.html', agent_id=agent_id, agent_name=agent[0], specialty=agent[1])

# RUTA 3: API para procesar mensajes (ACTUALIZADA CON RAG)
@app.route('/api/chat', methods=['POST'])
def process_chat():
    data = request.json
    agent_id = data.get('agent_id')
    user_message = data.get('message')

    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT system_prompt, model_version FROM agents WHERE id = %s", (agent_id,))
    agent_data = cur.fetchone()
    
    if not agent_data:
        return jsonify({"error": "Agente no válido"}), 400
        
    system_prompt, model_version = agent_data

    # --- INICIO LÓGICA DE MEMORIA (RAG) ---
    contexto_extra = ""
    try:
        # 1. Convertir la pregunta del usuario en un vector
        user_vector = get_embedding(user_message)
        
        # 2. Buscar en PostgreSQL los 3 fragmentos de PDF más relevantes 
        # usando el operador de distancia coseno (<=>) de pgvector
        cur.execute("""
            SELECT chunk_text 
            FROM knowledge_base 
            WHERE agent_id = %s 
            ORDER BY embedding <=> %s::vector 
            LIMIT 3
        """, (agent_id, str(user_vector)))
        
        resultados = cur.fetchall()
        
        # Si encontró documentos, armamos un contexto para inyectarle al prompt
        if resultados:
            fragmentos = "\n---\n".join([row[0] for row in resultados])
            contexto_extra = f"\n\nINFORMACIÓN DE CONTEXTO DE TUS DOCUMENTOS:\n{fragmentos}\n\nUsa esta información para responder a la consulta del usuario si es relevante."
    except Exception as e:
        print(f"Aviso: No se pudo recuperar contexto (probablemente el agente aún no tiene PDFs). Detalle: {e}")

    # Unimos la personalidad original del agente con la memoria extraída de los PDFs
    prompt_final = system_prompt + contexto_extra
    # --- FIN LÓGICA DE MEMORIA ---

    try:
        response = openai.ChatCompletion.create(
            model=model_version,
            messages=[
                {"role": "system", "content": prompt_final},
                {"role": "user", "content": user_message}
            ]
        )
        ai_response = response.choices[0].message.content
        
        # Guardar historial en BD
        cur.execute(
            "INSERT INTO chat_history (agent_id, user_message, ai_response) VALUES (%s, %s, %s)",
            (agent_id, user_message, ai_response)
        )
        conn.commit()
    except Exception as e:
        ai_response = f"Error del sistema: {str(e)}"

    cur.close()
    conn.close()
    
    return jsonify({"response": ai_response})

# RUTA 4: API para subir documentos (NUEVA)
@app.route('/api/upload_doc', methods=['POST'])
def upload_document():
    agent_id = request.form.get('agent_id')
    
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No se envió ningún archivo."}), 400
        
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"success": False, "error": "Archivo sin nombre."}), 400
        
    if file and file.filename.endswith('.pdf'):
        # Enviamos el archivo en memoria directamente al procesador (sin guardarlo en disco físico)
        resultado = process_and_store_document(agent_id, file.filename, file.stream)
        return jsonify(resultado)
    else:
        return jsonify({"success": False, "error": "Por el momento, solo se permiten archivos PDF."}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
