import requests
from datetime import datetime, timedelta

def obtener_dolar_semanal(conn):
    """
    conn: Conexión a tu base de datos PostgreSQL en Railway
    Busca la cotización, si tiene más de 7 días, la actualiza.
    """
    cursor = conn.cursor()
    
    # Asegurarnos de que la tabla exista (podes correr esto una vez o dejarlo acá)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            clave VARCHAR(50) PRIMARY KEY,
            valor NUMERIC,
            ultima_actualizacion TIMESTAMP
        )
    ''')
    
    cursor.execute("SELECT valor, ultima_actualizacion FROM configuracion WHERE clave = 'dolar_mep'")
    resultado = cursor.fetchone()
    
    ahora = datetime.now()
    
    # Si no existe el registro o pasaron más de 7 días (modificable a los días que quieras)
    if not resultado or (ahora - resultado[1]) > timedelta(days=7):
        try:
            # Usamos el dólar MEP como referencia B2B (podés cambiarlo por 'oficial' o 'tarjeta')
            response = requests.get("https://dolarapi.com/v1/dolares/mep")
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
            
            return float(nuevo_valor)
        except Exception as e:
            # Si se cae la API, intentamos usar el último valor guardado
            if resultado:
                return float(resultado[0])
            return 1000.0 # Valor de fallback de emergencia
            
    # Si tiene menos de 7 días, devolvemos el valor cacheado en DB
    return float(resultado[0])
