import json
import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dbname")

def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # 1. Activar la extensión vectorial
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    
    # 2. Tablas base (Actualizadas)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            email VARCHAR(100) UNIQUE,
            password_hash VARCHAR(255),
            subscription_plan VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agents (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            specialty VARCHAR(50),
            system_prompt TEXT,
            model_version VARCHAR(50) DEFAULT 'gpt-4o',
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chat_history (
            id SERIAL PRIMARY KEY,
            agent_id INTEGER REFERENCES agents(id),
            customer_id INTEGER REFERENCES customers(id),
            user_message TEXT,
            ai_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 3. NUEVA TABLA: El "Workspace" (Qué agentes instanció cada cliente)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_agents (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id),
            agent_id INTEGER REFERENCES agents(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(customer_id, agent_id)
        );
    """)

    # 4. Tabla de Memoria (RAG)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id SERIAL PRIMARY KEY,
            agent_id INTEGER REFERENCES agents(id),
            customer_id INTEGER REFERENCES customers(id),
            document_name VARCHAR(255),
            chunk_text TEXT,
            embedding vector(1536), 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Parche de seguridad automático por si las tablas ya existían en tu DB
    try:
        cur.execute("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS customer_id INTEGER REFERENCES customers(id);")
        cur.execute("ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS customer_id INTEGER REFERENCES customers(id);")
    except:
        pass

    conn.commit()
    print("Tablas verificadas. Aislamiento de datos (Multi-tenant) configurado.")

    # Sincronizar catálogo de agentes desde el JSON
    try:
        with open('agents_config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        for agent in data['agents']:
            cur.execute("""
                INSERT INTO agents (id, name, specialty, system_prompt) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                name = EXCLUDED.name, specialty = EXCLUDED.specialty, system_prompt = EXCLUDED.system_prompt;
            """, (agent['id'], agent['name'], agent['specialty'], agent['system_prompt']))
        
        conn.commit()
        print("Catálogo de agentes sincronizado.")
    except Exception as e:
        print(f"Aviso: No se pudo sincronizar agents_config.json. Detalle: {e}")

    cur.close()
    conn.close()

if __name__ == '__main__':
    init_db()
