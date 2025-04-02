#!/usr/bin/env python3

import os
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def mask_api_key(key):
    """Mask an API key for secure logging."""
    if not key:
        return None
    return key[:4] + "..." + key[-4:]

def check_env_before_loading():
    """Check environment variables before loading .env file."""
    logger.info("Checking environment variables BEFORE loading .env file...")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        logger.info("ANTHROPIC_API_KEY found in environment: %s", mask_api_key(anthropic_key))
    else:
        logger.info("ANTHROPIC_API_KEY not found in environment")
    return anthropic_key

def load_dotenv_file():
    """Load environment variables from .env file."""
    logger.info("Loading from .env file...")
    load_dotenv(override=True)

def check_env_after_loading():
    """Check environment variables after loading .env file."""
    logger.info("Checking environment variables AFTER loading .env file...")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        logger.info("ANTHROPIC_API_KEY found in environment: %s", mask_api_key(anthropic_key))
    else:
        logger.info("ANTHROPIC_API_KEY not found in environment after loading .env")
    return anthropic_key

def check_env_file_exists():
    """Check if .env file exists and inspect its contents."""
    logger.info("Checking if .env file exists...")
    if os.path.exists(".env"):
        logger.info(".env file found")
        with open(".env", "r", encoding="utf-8") as f:
            logger.info("Contents of .env file (looking for ANTHROPIC_API_KEY only):")
            for line in f:
                if "ANTHROPIC_API_KEY" in line and not line.strip().startswith("#"):
                    # Mask the actual key for security
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2 and parts[1]:
                        key = parts[1]
                        logger.info("Found ANTHROPIC_API_KEY in .env: %s", mask_api_key(key))
                    else:
                        logger.info("ANTHROPIC_API_KEY line found but no value set")
    else:
        logger.info(".env file not found")

def check_env():
    """Check environment variables and dotenv loading."""
    check_env_before_loading()
    load_dotenv_file()
    anthropic_key_after = check_env_after_loading()
    check_env_file_exists()
    
    # Final verdict
    if anthropic_key_after:
        logger.info("✅ ANTHROPIC_API_KEY is properly loaded and available")
        return True
    
    logger.error("❌ ANTHROPIC_API_KEY is NOT available after all loading attempts")
    return False

if __name__ == "__main__":
    check_env()