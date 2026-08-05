import requests
from datetime import datetime, timedelta

def obtener_dolar_semanal(conn):
    """
    conn: Conexión a tu base de datos PostgreSQL en Railway
    Busca la cotización, si tiene más de 7 días, la actualiza.
    """
    cursor = conn.cursor()
    
    # 1. Asegurarnos de que la tabla exista y GUARDAR (Commit) este cambio
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            clave VARCHAR(50) PRIMARY KEY,
            valor NUMERIC,
            ultima_actualizacion TIMESTAMP
        )
    ''')
    conn.commit() # ¡Este es el paso vital que faltaba!
    
    cursor.execute("SELECT valor, ultima_actualizacion FROM configuracion WHERE clave = 'dolar_mep'")
    resultado = cursor.fetchone()
    
    ahora = datetime.now()
    
    # Si no existe el registro o pasaron más de 7 días
    if not resultado or (ahora - resultado[1]) > timedelta(days=7):
        try:
            # 2. Agregar un "Disfraz" (Headers) para que DolarAPI no bloquee a Railway
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            
            # Agregamos un timeout de 10 segundos por si la API está caída
            response = requests.get("https://dolarapi.com/v1/dolares/mep", headers=headers, timeout=10)
            response.raise_for_status() # Chequea que la respuesta sea 200 OK
            
            data = response.json()
            nuevo_valor = data['venta']
            
            # Guardamos el nuevo valor en PostgreSQL
            cursor.execute('''
                INSERT INTO configuracion (clave, valor, ultima_actualizacion) 
                VALUES ('dolar_mep', %s, %s)
                ON CONFLICT (clave) DO UPDATE 
                SET valor = EXCLUDED.valor, ultima_actualizacion = EXCLUDED.ultima_actualizacion
            ''', (nuevo_valor, ahora))
            conn.commit()
            
            print(f"✅ Éxito: Dólar actualizado en la DB a {nuevo_valor}")
            return float(nuevo_valor)
            
        except Exception as e:
            # Si falla la API, ahora nos dejará el rastro en los Logs de Railway
            print(f"⚠️ Error actualizando dólar (usando fallback): {e}")
            
            # Si ya teníamos un valor viejo guardado, es mejor usar ese
            if resultado:
                return float(resultado[0])
                
            # Si nunca se pudo guardar nada, usamos un fallback más realista (ej: 1300)
            return 1300.0 
            
    # Si tiene menos de 7 días, devolvemos el valor cacheado en DB
    print(f"⚡ Usando dólar cacheado en DB: {resultado[0]}")
    return float(resultado[0])
