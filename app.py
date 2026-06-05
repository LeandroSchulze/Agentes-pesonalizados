import os
import psycopg2
import openai
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps

# NUEVO: Herramientas para encriptar contraseñas de forma segura
from werkzeug.security import generate_password_hash, check_password_hash

# Importamos las funciones de nuestro procesador de documentos
from document_processor import process_and_store_document, get_embedding

app = Flask(__name__)

# LLAVE DE SEGURIDAD: Necesaria para encriptar la sesión del usuario
app.secret_key = os.getenv("SECRET_KEY", "super_secreto_mvp_2026")

openai.api_key = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dbname")

# CREDENCIALES DE ADMIN (Acceso Maestro)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@tuempresa.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# NUEVO: Actualización automática de la base de datos para soportar contraseñas
try:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);")
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print(f"Aviso DB (Ignorar si ya existe): {e}")

# DECORADOR DE SEGURIDAD
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# RUTA DE SEGURIDAD: Iniciar Sesión (ACTUALIZADA PARA LEER LA BASE DE DATOS)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # 1. Chequeo de Administrador Maestro
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['user_role'] = 'admin'
            return redirect(url_for('dashboard'))
            
        # 2. Chequeo de Clientes en PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, password_hash FROM customers WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        # Verificamos si el usuario existe y si la contraseña coincide con el hash
        if user and user[1] and check_password_hash(user[1], password):
            session['logged_in'] = True
            session['customer_id'] = user[0]
            session['user_role'] = 'customer'
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Correo o contraseña incorrectos")
            
    return render_template('login.html')

# RUTA DE SEGURIDAD: Crear Cuenta (NUEVA)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Encriptamos la contraseña antes de guardarla
        hashed_pw = generate_password_hash(password)
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO customers (name, email, password_hash, subscription_plan) VALUES (%s, %s, %s, %s)",
                (name, email, hashed_pw, 'Free')
            )
            conn.commit()
            
            # Autologueo después de registrarse
            cur.execute("SELECT id FROM customers WHERE email = %s", (email,))
            new_user = cur.fetchone()
            session['logged_in'] = True
            session['customer_id'] = new_user[0]
            session['user_role'] = 'customer'
            
            return redirect(url_for('dashboard'))
            
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return render_template('register.html', error="Este correo ya está registrado en la plataforma.")
        except Exception as e:
            conn.rollback()
            return render_template('register.html', error=f"Error interno: {str(e)}")
        finally:
            cur.close()
            conn.close()
            
    return render_template('register.html')

# RUTA DE SEGURIDAD: Cerrar Sesión
@app.route('/logout')
def logout():
    session.clear() # Limpia todos los datos de la sesión
    return redirect(url_for('login'))

# RUTA 1: Dashboard de Administración
@app.route('/')
@login_required
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

    # --- INICIO LÓGICA DE MEMORIA (RAG) ---
    contexto_extra = ""
    try:
        user_vector = get_embedding(user_message)
        cur.execute("""
            SELECT chunk_text 
            FROM knowledge_base 
            WHERE agent_id = %s 
            ORDER BY embedding <=> %s::vector 
            LIMIT 3
        """, (agent_id, str(user_vector)))
        
        resultados = cur.fetchall()
        
        if resultados:
            fragmentos = "\n---\n".join([row[0] for row in resultados])
            contexto_extra = f"\n\nINFORMACIÓN DE CONTEXTO DE TUS DOCUMENTOS:\n{fragmentos}\n\nUsa esta información para responder a la consulta del usuario si es relevante."
    except Exception as e:
        print(f"Aviso: No se pudo recuperar contexto. Detalle: {e}")

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

# RUTA 4: API para subir documentos 
@app.route('/api/upload_doc', methods=['POST'])
def upload_document():
    agent_id = request.form.get('agent_id')
    
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No se envió ningún archivo."}), 400
        
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"success": False, "error": "Archivo sin nombre."}), 400
        
    if file and file.filename.endswith('.pdf'):
        resultado = process_and_store_document(agent_id, file.filename, file.stream)
        return jsonify(resultado)
    else:
        return jsonify({"success": False, "error": "Por el momento, solo se permiten archivos PDF."}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
