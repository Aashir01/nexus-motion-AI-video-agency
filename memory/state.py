import os
import redis
from dotenv import load_dotenv

load_dotenv()

class BrandDatabase:
    """
    Manages state persistence for the 'Afsana Labs' aesthetic and Character Seed IDs
    using a Redis instance. Includes an in-memory fallback.
    """
    def __init__(self):
        redis_uri = os.getenv("REDIS_URI")
        
        # In-memory fallback if Redis is unavailable
        self.fallback_db = {}
        
        if not redis_uri or redis_uri == "your_aiven_redis_uri_here":
            print("Warning: REDIS_URI not configured. Using in-memory fallback database.")
            self.redis_client = None
        else:
            try:
                self.redis_client = redis.from_url(redis_uri, decode_responses=True)
                self.redis_client.ping()
                print("✅ Connected to Redis Brand Database successfully.")
            except Exception as e:
                print(f"⚠️  Failed to connect to Redis ({e}). Using in-memory fallback database.")
                self.redis_client = None

    def set_seed(self, niche: str, seed_id: str) -> None:
        key = f"nexus_seed:{niche.lower().replace(' ', '_')}"
        if self.redis_client:
            try:
                self.redis_client.set(key, seed_id)
            except Exception as e:
                print(f"Redis set error: {e}")
                self.fallback_db[key] = seed_id
        else:
            self.fallback_db[key] = seed_id

    def get_seed(self, niche: str) -> str:
        key = f"nexus_seed:{niche.lower().replace(' ', '_')}"
        if self.redis_client:
            try:
                val = self.redis_client.get(key)
                return val.decode("utf-8") if val else None
            except Exception as e:
                print(f"Redis get error: {e}")
                return self.fallback_db.get(key)
        else:
            return self.fallback_db.get(key)

# Global instance for easy import across nodes
brand_db = BrandDatabase()
