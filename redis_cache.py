"""
Redis cache manager for blood test kit advisor.
Handles caching of LLM responses to improve performance and reduce API costs.
"""
import json
import hashlib
import atexit
import logging
from typing import Any, Optional, Callable, Dict
import redis

# Configure logger
logger = logging.getLogger('blood_test_kit_advisor.cache')

# Global Redis client
redis_client = None
DEFAULT_TTL = 60 * 60 * 24  # 24 hours in seconds

def initialize_redis(host='localhost', port=6379, db=0, ttl=DEFAULT_TTL):
    """
    Initialize Redis connection.
    
    Args:
        host: Redis host address
        port: Redis port
        db: Redis database number
        ttl: Default time-to-live for cache entries in seconds
        
    Returns:
        bool: Success status
    """
    global redis_client, DEFAULT_TTL
    DEFAULT_TTL = ttl
    
    try:
        redis_client = redis.Redis(host=host, port=port, db=db)
        # Test connection
        redis_client.ping()
        logger.info(f"Redis cache initialized successfully at {host}:{port}")
        
        # Register cleanup on program exit
        atexit.register(cleanup)
        return True
    except redis.ConnectionError as e:
        logger.warning(f"Failed to connect to Redis server: {e}")
        return False
    except Exception as e:
        logger.warning(f"Redis initialization error: {e}")
        return False

def generate_cache_key(cache_type: str, data: Any) -> str:
    """
    Generate a deterministic cache key based on input data.
    
    Args:
        cache_type: Type of cache entry (e.g., 'cost_analysis', 'biomarkers')
        data: The data to hash for the key
        
    Returns:
        str: Cache key
    """
    serialized = json.dumps(data, sort_keys=True)
    hash_value = hashlib.md5(serialized.encode()).hexdigest()
    return f"bloodtest:{cache_type}:{hash_value}"

def get_cached(cache_type: str, data: Any) -> Optional[str]:
    """
    Retrieve value from cache if available.
    
    Args:
        cache_type: Type of cache entry
        data: The data used for retrieving the cache key
        
    Returns:
        str or None: Cached value if found, None otherwise
    """
    if not redis_client:
        return None
    
    key = generate_cache_key(cache_type, data)
    
    try:
        value = redis_client.get(key)
        if value:
            logger.debug(f"Cache hit for {cache_type}")
            return value.decode('utf-8')
        logger.debug(f"Cache miss for {cache_type}")
        return None
    except Exception as e:
        logger.warning(f"Error retrieving from cache: {e}")
        return None

def set_cached(cache_type: str, data: Any, value: str, ttl: int = None) -> bool:
    """
    Store value in cache.
    
    Args:
        cache_type: Type of cache entry
        data: The data used for generating the cache key
        value: Value to store in cache
        ttl: Time-to-live in seconds, or None for default
        
    Returns:
        bool: Success status
    """
    if not redis_client:
        return False
    
    key = generate_cache_key(cache_type, data)
    ttl = ttl or DEFAULT_TTL
    
    try:
        redis_client.setex(key, ttl, value)
        logger.debug(f"Stored in cache: {cache_type}")
        return True
    except Exception as e:
        logger.warning(f"Error storing in cache: {e}")
        return False

async def cached_or_compute(cache_type: str, 
                           data: Any, 
                           compute_func: Callable,
                           ttl: int = None) -> str:
    """
    Get cached result or compute and cache it.
    
    Args:
        cache_type: Type of cache entry
        data: Data used for generating cache key and for computation
        compute_func: Async function to compute result if not in cache
        ttl: Time-to-live in seconds
        
    Returns:
        str: Result from cache or computation
    """
    if not redis_client:
        # Redis not available, just compute directly
        return await compute_func()
    
    # Try to get from cache
    cached_result = get_cached(cache_type, data)
    if cached_result:
        return cached_result
    
    # Not in cache, compute the result
    result = await compute_func()
    
    # Store in cache for future requests
    set_cached(cache_type, data, result, ttl)
    
    return result

def invalidate_cache(cache_type: str = None):
    """
    Invalidate cache entries by type or all if type not specified.
    
    Args:
        cache_type: Type of cache to invalidate or None for all
    """
    if not redis_client:
        return
    
    try:
        if cache_type:
            pattern = f"bloodtest:{cache_type}:*"
            keys = redis_client.keys(pattern)
            if keys:
                redis_client.delete(*keys)
                logger.info(f"Invalidated {len(keys)} {cache_type} cache entries")
        else:
            pattern = "bloodtest:*"
            keys = redis_client.keys(pattern)
            if keys:
                redis_client.delete(*keys)
                logger.info(f"Invalidated all {len(keys)} cache entries")
    except Exception as e:
        logger.warning(f"Error invalidating cache: {e}")

def cleanup():
    """
    Perform cleanup when program ends.
    """
    global redis_client
    
    if redis_client:
        try:
            logger.info("Closing Redis connection")
            redis_client.close()
            redis_client = None
        except Exception as e:
            logger.warning(f"Error during Redis cleanup: {e}")

# Example usage in main application:
"""
from redis_cache import initialize_redis, cached_or_compute

# Initialize cache at startup
initialize_redis()

# In analyze_cost_effectiveness method:
async def analyze_cost_effectiveness(self) -> str:
    async def compute_analysis():
        products_json = json.dumps(self.products["products"], indent=2)
        messages = [
            {"role": "system", "content": self.openai_system_prompt},
            {"role": "user", "content": f"Analyze the cost-effectiveness of these blood test packages..."}
        ]
        return await self.openai_query(messages)
    
    # Use the caching mechanism
    return await cached_or_compute("cost_analysis", self.products["products"], compute_analysis)
""" 