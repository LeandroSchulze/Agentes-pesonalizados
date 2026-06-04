import json
import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dbname")

def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # Crear tablas
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            email VARCHAR(100) UNIQUE,
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
            user_message TEXT,
            ai_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    print("Tablas verificadas/creadas.")

    # Sincronizar agentes desde el JSON
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
    print("Agentes sincronizados.")
    cur.close()
    conn.close()

if __name__ == '__main__':
    init_db()
