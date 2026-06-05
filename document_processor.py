import os
import openai
import psycopg2
import PyPDF2
from io import BytesIO

openai.api_key = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dbname")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def extract_text_from_pdf(file_stream):
    """Extrae todo el texto de un PDF en memoria."""
    reader = PyPDF2.PdfReader(file_stream)
    text = ""
    for page in reader.pages:
        if page.extract_text():
            text += page.extract_text() + "\n"
    return text

def chunk_text(text, chunk_size=1000, overlap=200):
    """Corta el texto en pedazos para que la IA lo pueda procesar sin perder contexto."""
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def get_embedding(text):
    """Llama a OpenAI para convertir el texto en un vector matemático."""
    response = openai.Embedding.create(
        input=text,
        model="text-embedding-ada-002"
    )
    return response['data'][0]['embedding']

def process_and_store_document(agent_id, filename, file_stream):
    """Función principal: orquesta la extracción, vectorización y guardado."""
    try:
        # 1. Extraer texto
        raw_text = extract_text_from_pdf(file_stream)
        if not raw_text.strip():
            return {"success": False, "error": "El documento parece estar vacío o no es legible."}

        # 2. Cortar en fragmentos
        chunks = chunk_text(raw_text)

        # 3. Guardar en Base de Datos
        conn = get_db_connection()
        cur = conn.cursor()

        for chunk in chunks:
            # Conseguir el vector numérico del fragmento
            vector = get_embedding(chunk)
            
            # Guardarlo en PostgreSQL
            cur.execute("""
                INSERT INTO knowledge_base (agent_id, document_name, chunk_text, embedding)
                VALUES (%s, %s, %s, %s)
            """, (agent_id, filename, chunk, vector))
            
        conn.commit()
        cur.close()
        conn.close()
        
        return {"success": True, "message": f"Documento '{filename}' procesado e incorporado a la memoria del agente."}
        
    except Exception as e:
        return {"success": False, "error": str(e)}
