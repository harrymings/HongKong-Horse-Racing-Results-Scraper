from db import get_engine

def check_db():
    try:
        eng = get_engine()
        with eng.connect() as conn:
            conn.execute("SELECT 1")
        print("DB OK")
        return 0
    except Exception as e:
        print("DB connection failed:", e)
        return 1

if __name__ == '__main__':
    raise SystemExit(check_db())
