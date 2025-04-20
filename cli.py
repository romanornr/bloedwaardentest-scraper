#!/usr/bin/env python3

import asyncio
import argparse
import sys
import os
import logging
from dotenv import load_dotenv

# Load environment variables first, before any other imports
load_dotenv(override=True)

# Verify the ANTHROPIC_API_KEY is loaded
api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY not found in environment or .env file")
    print("Please ensure your .env file contains a valid ANTHROPIC_API_KEY")
    sys.exit(1)
else:
    print(f"ANTHROPIC_API_KEY loaded successfully (starts with: {api_key[:4]}...)")

# Now import the analyzer after environment is set up
from blood_test_kit_advisor import BloodTestKitAdvisor

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Blood Test Kit Advisor - Multi-agent system for recommending blood test kits"
    )
    
    parser.add_argument(
        "--query", "-q",
        type=str,
        help="Specific query about blood test packages"
    )
    
    parser.add_argument(
        "--file", "-f",
        type=str,
        default="data/products.json",
        help="Path to the JSON file containing blood test products (default: data/products.json)"
    )
    
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Start in interactive mode"
    )
    
    return parser.parse_args()

async def interactive_mode(advisor):
    """Run the advisor in interactive mode."""
    print("\n===== Blood Test Kit Advisor - Interactive Mode =====")
    print("Type 'exit' or 'quit' to end the session")
    print("Example queries:")
    print("  - Which blood test package gives the most comprehensive coverage?")
    print("  - What is the most cost-effective package for basic health monitoring?")
    print("  - Identify packages optimized for cardiovascular health monitoring")
    print("  - Which packages provide unique biomarkers not available in standard panels?")
    print("\nEnter your query:")
    
    while True:
        try:
            query = input("> ").strip()
            
            if query.lower() in ["exit", "quit"]:
                print("Exiting...")
                break
            
            if not query:
                continue
            
            try:
                print("Processing query... (this may take a minute)")
                response = await advisor.recommend_packages(query)
                print("\n" + "=" * 40)
                print(response)
                print("=" * 40 + "\n")
            except Exception as e:
                logger.error(f"Error processing query: {e}")
                print(f"Error: Unable to process your query. Please try again with a simpler question.")
                print(f"Technical details: {str(e)}")
        except (EOFError, KeyboardInterrupt):
            print("\nDetected keyboard interrupt or EOF. Exiting...")
            break
        except Exception as e:
            logger.error(f"Unexpected error in interactive mode: {e}")
            print(f"An unexpected error occurred. Please try again.")
            # Continue the loop to allow more queries

async def run_query(advisor, query):
    """Run a single query and display the result."""
    try:
        print(f"Query: {query}")
        print("Processing... (this may take a minute)")
        response = await advisor.recommend_packages(query)
        print("\n" + "=" * 40)
        print(response)
        print("=" * 40)
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        print(f"Error: {e}")
        sys.exit(1)

async def main():
    """Main function to run the Blood Test Kit Advisor CLI."""
    args = parse_args()
    
    try:
        advisor = BloodTestKitAdvisor(data_path=args.file)
        
        if args.interactive:
            await interactive_mode(advisor)
        elif args.query:
            await run_query(advisor, args.query)
        else:
            print("Please provide a query with --query or use --interactive mode.")
            print("Run with --help for more information.")
            sys.exit(1)
    
    except FileNotFoundError as e:
        logger.error(f"Error: {e}")
        print(f"Error: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Error: {e}")
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())