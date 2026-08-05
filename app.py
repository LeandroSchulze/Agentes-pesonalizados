import os
import psycopg2
import openai
import mercadopago
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from document_processor import process_and_store_document, get_embedding
from utils import obtener_dolar_semanal

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "super_secreto_mvp_2026")
openai.api_key = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dbname")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@tuempresa.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")

# Inicializamos MercadoPago con la variable de Railway
mp_access_token = os.getenv("MP_ACCESS_TOKEN")
sdk = mercadopago.SDK(mp_access_token) if mp_access_token else None

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Preparamos la base de datos para las suscripciones
try:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);")
    cur.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS plan_activo BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS limite_agentes INT DEFAULT 0;")
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print(f"Aviso DB (Ignorar si ya existe): {e}")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['user_role'] = 'admin'
            return redirect(url_for('dashboard'))
            
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, password_hash FROM customers WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and user[1] and check_password_hash(user[1], password):
            session['logged_in'] = True
            session['customer_id'] = user[0]
            session['user_role'] = 'customer'
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Correo o contraseña incorrectos")
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        hashed_pw = generate_password_hash(password)
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO customers (name, email, password_hash, subscription_plan, plan_activo) VALUES (%s, %s, %s, %s, %s)",
                (name, email, hashed_pw, 'Free', False)
            )
            conn.commit()
            
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

@app.route('/logout')
def logout():
    session.clear() 
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    customer_id = session.get('customer_id')
    conn = get_db_connection()
    cur = conn.cursor()
    
    mis_agentes = []
    catalogo = []
    docs_por_agente = {}

    if customer_id:
        cur.execute("""
            SELECT a.id, a.name, a.specialty, a.status 
            FROM agents a 
            JOIN customer_agents ca ON a.id = ca.agent_id 
            WHERE ca.customer_id = %s
            ORDER BY a.id;
        """, (customer_id,))
        mis_agentes = cur.fetchall()

        cur.execute("""
            SELECT id, name, specialty, status 
            FROM agents 
            WHERE id NOT IN (SELECT agent_id FROM customer_agents WHERE customer_id = %s)
            ORDER BY id;
        """, (customer_id,))
        catalogo = cur.fetchall()

        cur.execute("""
            SELECT agent_id, document_name 
            FROM knowledge_base 
            WHERE customer_id = %s 
            GROUP BY agent_id, document_name;
        """, (customer_id,))
        docs_raw = cur.fetchall()
        
        for row in docs_raw:
            a_id = row[0]
            d_name = row[1]
            if a_id not in docs_por_agente:
                docs_por_agente[a_id] = []
            docs_por_agente[a_id].append(d_name)

    else:
        cur.execute("SELECT id, name, specialty, status FROM agents ORDER BY id;")
        catalogo = cur.fetchall()

    cur.close()
    conn.close()
    return render_template('dashboard.html', mis_agentes=mis_agentes, catalogo=catalogo, docs_por_agente=docs_por_agente)

@app.route('/api/instanciar', methods=['POST'])
@login_required
def instanciar_agente():
    data = request.json
    agent_id = data.get('agent_id')
    customer_id = session.get('customer_id')

    if not customer_id:
        return jsonify({"success": False, "error": "Acceso denegado."}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO customer_agents (customer_id, agent_id) VALUES (%s, %s)",
            (customer_id, agent_id)
        )
        conn.commit()
        return jsonify({"success": True})
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"success": False, "error": "El agente ya está en tu workspace."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/agent/<int:agent_id>')
@login_required
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

@app.route('/api/chat', methods=['POST'])
@login_required
def process_chat():
    data = request.json
    agent_id = data.get('agent_id')
    user_message = data.get('message')
    customer_id = session.get('customer_id') 

    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM chat_history WHERE customer_id = %s AND created_at >= CURRENT_DATE", (customer_id,))
    daily_msgs = cur.fetchone()[0]
    
    if daily_msgs >= 50:
        cur.close()
        conn.close()
        return jsonify({"response": "Has alcanzado el límite diario de 50 consultas de tu plan. Por favor, contacta a soporte para ampliar tu capacidad."})

    cur.execute("SELECT system_prompt, model_version FROM agents WHERE id = %s", (agent_id,))
    agent_data = cur.fetchone()
    
    if not agent_data:
        return jsonify({"error": "Agente no válido"}), 400
        
    system_prompt, model_version = agent_data

    contexto_extra = ""
    try:
        user_vector = get_embedding(user_message)
        cur.execute("""
            SELECT chunk_text 
            FROM knowledge_base 
            WHERE agent_id = %s AND customer_id = %s
            ORDER BY embedding <=> %s::vector 
            LIMIT 3
        """, (agent_id, customer_id, str(user_vector)))
        
        resultados = cur.fetchall()
        
        if resultados:
            fragmentos = "\n---\n".join([row[0] for row in resultados])
            contexto_extra = f"\n\nINFORMACIÓN DE CONTEXTO DE TUS DOCUMENTOS:\n{fragmentos}\n\nUsa esta información para responder a la consulta del usuario si es relevante."
    except Exception as e:
        print(f"Aviso: No se pudo recuperar contexto. Detalle: {e}")

    prompt_final = system_prompt + contexto_extra

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
            "INSERT INTO chat_history (agent_id, customer_id, user_message, ai_response) VALUES (%s, %s, %s, %s)",
            (agent_id, customer_id, user_message, ai_response)
        )
        conn.commit()
    except Exception as e:
        ai_response = f"Error del sistema: {str(e)}"

    cur.close()
    conn.close()
    
    return jsonify({"response": ai_response})

@app.route('/api/upload_doc', methods=['POST'])
@login_required
def upload_document():
    agent_id = request.form.get('agent_id')
    customer_id = session.get('customer_id')
    
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No se envió ningún archivo."}), 400
        
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"success": False, "error": "Archivo sin nombre."}), 400
        
    if file and file.filename.endswith('.pdf'):
        resultado = process_and_store_document(agent_id, customer_id, file.filename, file.stream)
        return jsonify(resultado)
    else:
        return jsonify({"success": False, "error": "Solo se permiten archivos PDF."}), 400

@app.route('/api/delete_doc', methods=['POST'])
@login_required
def delete_document():
    data = request.json
    agent_id = data.get('agent_id')
    doc_name = data.get('document_name')
    customer_id = session.get('customer_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM knowledge_base WHERE customer_id = %s AND agent_id = %s AND document_name = %s", 
                   (customer_id, agent_id, doc_name))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": "No se pudo eliminar el documento."})
    finally:
        cur.close()
        conn.close()


# ==========================================
# RUTAS DE FACTURACIÓN Y PAGOS (MERCADOPAGO)
# ==========================================

@app.route('/api/generar_pago', methods=['POST'])
@login_required
def generar_pago():
    customer_id = session.get('customer_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Traemos la cotización actualizada
        cotizacion_actual = obtener_dolar_semanal(conn)
        
        # 2. Verificamos si ya tiene agentes para definir la variable de precio
        cur.execute("SELECT limite_agentes FROM customers WHERE id = %s", (customer_id,))
        resultado = cur.fetchone()
        agentes_actuales = resultado[0] if resultado and resultado[0] else 0
        
        # 3. Lógica de variables: USD 50 el primero, USD 40 los adicionales
        if agentes_actuales >= 1:
            precio_base_usd = 40
            titulo_item = "Suscripción PRO - Agente Adicional"
        else:
            precio_base_usd = 50
            titulo_item = "Suscripción PRO - 1 Agente de IA"
            
        precio_ars = precio_base_usd * cotizacion_actual
        
    except Exception as e:
        print("Error obteniendo datos:", e)
        # Fallback de emergencia por si falla la API
        precio_base_usd = 50 
        precio_ars = precio_base_usd * 1000.0 
        titulo_item = "Suscripción PRO - 1 Agente de IA"
    finally:
        cur.close()
        conn.close()
        
    if not sdk:
        return jsonify({"error": "SDK de MercadoPago no configurado. Falta el Token."}), 500

    # 4. Armamos la preferencia con el precio dinámico calculado
    preference_data = {
        "items": [
            {
                "title": titulo_item,
                "description": f"Licencia mensual operativa en Process Intelligence (Valor base: USD {precio_base_usd})",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": round(precio_ars, 2)
            }
        ],
        "back_urls": {
            "success": "https://www.agentsaipro.com/?pago=exito",
            "failure": "https://www.agentsaipro.com/?pago=fallo",
            "pending": "https://www.agentsaipro.com/?pago=pendiente"
        },
        "auto_return": "approved",
        "external_reference": str(customer_id), 
        "notification_url": "https://www.agentsaipro.com/api/webhooks/mercadopago"
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        return jsonify({"checkout_url": preference_response["response"]["init_point"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/webhooks/mercadopago', methods=['POST'])
def mp_webhook():
    action = request.args.get('action') or request.args.get('type')
    data_id = request.args.get('data.id') or request.args.get('id')

    if action in ['payment', 'payment.created', 'payment.updated']:
        try:
            payment_info = sdk.payment().get(data_id)
            if payment_info["status"] == 200:
                estado_pago = payment_info["response"]["status"]
                
                if estado_pago == "approved":
                    user_id = payment_info["response"]["external_reference"]
                    
                    conn = get_db_connection()
                    cur = conn.cursor()
                    
                    # MAGIA: Le sumamos 1 al límite actual (limite_agentes + 1)
                    cur.execute("""
                        UPDATE customers 
                        SET subscription_plan = 'Pro', 
                            plan_activo = TRUE, 
                            limite_agentes = COALESCE(limite_agentes, 0) + 1 
                        WHERE id = %s
                    """, (user_id,))
                    conn.commit()
                    cur.close()
                    conn.close()
                    print(f"¡Pago aprobado y agente agregado para el usuario {user_id}!")
        except Exception as e:
            print(f"Error procesando webhook de MP: {e}")

    return jsonify({"status": "recibido"}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)
