import os
import psycopg2
import openai
from flask import Flask, render_template, request, jsonify

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

# RUTA 3: API para procesar mensajes
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

    try:
        response = openai.ChatCompletion.create(
            model=model_version,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        )
        ai_response = response.choices[0].message.content
        
        # Guardar en BD
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
