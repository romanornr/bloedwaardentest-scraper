import os
import json
from pathlib import Path
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from typing import List, Dict, Any, Optional, Union
import logging
from colorlog import ColoredFormatter

# Environment variables should be loaded by the calling script
# This ensures we don't have duplicate loading or timing issues
if not os.getenv("ANTHROPIC_API_KEY"):
    print("WARNING: ANTHROPIC_API_KEY not found - this should have been loaded by the calling script")
    # Try to load it here as a fallback
    load_dotenv(override=True)
    if os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY loaded successfully as fallback")
    else:
        print("CRITICAL: Failed to load ANTHROPIC_API_KEY even after fallback attempt")

def setup_logger(name='blood_test_kit_advisor', log_file='data/advisor.log'):
    """
    Set up a logger with colored output for console and detailed logging for file
    """
    # Create data directory if it doesn't exist
    log_dir = Path(log_file).parent
    log_dir.mkdir(exist_ok=True)
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers to avoid duplicates
    if logger.handlers:
        logger.handlers = []
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Color scheme for different log levels
    console_colors = {
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    }
    
    # Console formatter with colors
    console_formatter = ColoredFormatter(
        "%(log_color)s%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%H:%M:%S",
        log_colors=console_colors,
        reset=True,
        style='%'
    )
    console_handler.setFormatter(console_formatter)
    
    # File handler with detailed formatting
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    
    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# Create logger
logger = setup_logger()

def log_section(title, char='═', width=80):
    """
    Create a visually distinct section in the logs and terminal output
    """
    padding = (width - len(title) - 2) // 2
    separator = char * width
    logger.info(separator)
    logger.info(f"{char * padding} {title} {char * padding}")
    logger.info(separator)
    # Also print to terminal for direct visibility
    print(separator)
    print(f"{char * padding} {title} {char * padding}")
    print(separator)

def log_model_init(model_type, model_name, success=True):
    """
    Log model initialization in a visually appealing way
    """
    if success:
        status = "✓"
        status_msg = "SUCCESS"
    else:
        status = "✗"
        status_msg = "FAILED"
    
    logger.info(f"┌─ {model_type} Model Initialization ───────────────────────")
    logger.info(f"│ Status: {status} {status_msg}")
    logger.info(f"│ Model:  {model_name}")
    logger.info("└───────────────────────────────────────────────────────")

class BloodTestKitAdvisor:
    """
    Multi-agent system for recommending blood test kits using Claude, OpenAI, and Gemini.
    """
    
    def __init__(self, data_path: str = "data/products.json"):
        """
        Initialize the advisor with API clients and load dataset.
        
        Args:
            data_path: Path to the JSON file containing blood test products
        """
        log_section("Initializing Blood Test Kit Advisor")
        
        # Check for API keys
        self._check_api_keys()
        
        # Initialize LLM clients
        self.claude, claude_success = initialize_claude()
        if not claude_success:
            raise ValueError("Claude initialization failed, cannot continue")
        
        # Initialize OpenAI and Gemini models
        self.openai, _ = initialize_openai()
        self.gemini, _ = initialize_gemini()
        
        # Load dataset
        self.data_path = data_path
        self.products = self._load_products()
        logger.info(f"Loaded dataset with {len(self.products.get('products', []))} products")
        
        # Set up system prompts for each agent
        self.claude_system_prompt = """
        You are an expert medical advisor specializing in blood test analysis and recommendations.
        Your role is to help users understand which blood test packages would be most appropriate for 
        their specific health needs, taking into consideration biomarker coverage, cost-effectiveness, 
        and scientific relevance.
        
        When providing recommendations:
        1. Consider the user's specific health concerns or goals if provided
        2. Evaluate cost-effectiveness (biomarkers per euro)
        3. Consider comprehensive coverage of important health markers
        4. Provide clear, evidence-based explanations for your recommendations
        
        You can delegate numerical analysis tasks to OpenAI and biomarker categorization to Gemini.
        Your final recommendations should synthesize all available information.
        """
        
        self.openai_system_prompt = """
        You are a data analysis expert specializing in numerical evaluation of blood test packages.
        Your role is to analyze blood test packages quantitatively and provide structured comparisons.
        
        When performing analysis:
        1. Calculate cost per biomarker for each package
        2. Identify overlaps and unique biomarkers between packages
        3. Find optimal combinations for complete biomarker coverage
        4. Present data in a structured, quantitative format
        
        Respond with clear numerical analysis and data-driven insights.
        """
        
        self.gemini_system_prompt = """
        You are a biomedical expert specializing in categorizing and explaining blood biomarkers.
        Your role is to provide scientific context for biomarkers and organize them into 
        functional health categories.
        
        When analyzing biomarkers:
        1. Group biomarkers by health function (cardiovascular, metabolic, hormonal, etc.)
        2. Explain the significance of specific biomarkers
        3. Identify complementary biomarker groupings
        4. Highlight unique or specialized biomarkers
        
        Respond with scientifically accurate categorizations and explanations.
        """
        
        log_section("Initialization Complete")
    
    def _check_api_keys(self):
        """Check if all required API keys are available."""
        # Claude is the only truly required key
        if not os.getenv("ANTHROPIC_API_KEY"):
            logger.error("Claude model unavailable - this is the primary agent and is required")
            logger.error("Please check your .env file contains ANTHROPIC_API_KEY")
            raise ValueError("ANTHROPIC_API_KEY is required for this application to function")
            
        # Log status of all potential keys for debugging
        all_keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]
        for key in all_keys:
            value = os.getenv(key)
            status = "✓ Found" if value else "✗ Missing"
            # Mask the actual API key values for security
            if value:
                masked_value = value[:4] + "..." + value[-4:]
            else:
                masked_value = "None"
            logger.info(f"{key}: {status} ({masked_value if value else 'Not provided'})")
        
        # Check which optional keys are missing
        missing_optional = []
        if not os.getenv("OPENAI_API_KEY"):
            missing_optional.append("OPENAI_API_KEY")
        if not os.getenv("GEMINI_API_KEY"):
            missing_optional.append("GEMINI_API_KEY")
            
        if missing_optional:
            logger.warning(f"Missing optional API keys: {', '.join(missing_optional)}")
            logger.warning("Claude will be used as a fallback for these capabilities")
            logger.warning("For best results, consider adding these keys to your .env file")
    
    def _load_products(self) -> Dict[str, Any]:
        """Load product data from JSON file."""
        data_file = Path(self.data_path)
        
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {self.data_path}")
        
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logger.info(f"Loaded {len(data['products'])} products from {self.data_path}")
        return data
    
    async def claude_query(self, messages: List[Dict[str, str]]) -> str:
        """
        Send a query to Claude and get a response.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            
        Returns:
            Claude's response text
        """
        langchain_messages = []
        
        for message in messages:
            if message["role"] == "system":
                langchain_messages.append(SystemMessage(content=message["content"]))
            elif message["role"] == "user":
                langchain_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                langchain_messages.append(AIMessage(content=message["content"]))
        
        response = self.claude.invoke(langchain_messages)
        return response.content
    
    async def openai_query(self, messages: List[Dict[str, str]]) -> str:
        """
        Send a query to OpenAI and get a response.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            
        Returns:
            OpenAI's response text or a fallback message if OpenAI is not available
        """
        # Check if OpenAI is initialized
        if not self.openai:
            logger.warning("OpenAI API key not provided - using Claude as fallback for numerical analysis")
            # Extract the user message content for Claude
            user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
            # Create a Claude-specific prompt
            claude_messages = [
                {"role": "system", "content": "You are providing numerical analysis as a fallback for OpenAI. Please analyze the data quantitatively."},
                {"role": "user", "content": f"Perform numerical analysis on the following data (as a fallback for OpenAI):\n\n{user_content}"}
            ]
            # Use Claude as fallback
            return await self.claude_query(claude_messages)
        
        langchain_messages = []
        
        for message in messages:
            if message["role"] == "system":
                langchain_messages.append(SystemMessage(content=message["content"]))
            elif message["role"] == "user":
                langchain_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                langchain_messages.append(AIMessage(content=message["content"]))
        
        response = self.openai.invoke(langchain_messages)
        return response.content
    
    async def gemini_query(self, messages: List[Dict[str, str]]) -> str:
        """
        Send a query to Gemini and get a response.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            
        Returns:
            Gemini's response text or a fallback message if Gemini is not available
        """
        # Check if Gemini is initialized
        if not self.gemini:
            logger.warning("Gemini API not available - using Claude as fallback for biomarker categorization")
            return await self._use_claude_as_gemini_fallback(messages)
            
        try:
            langchain_messages = []
            
            for message in messages:
                if message["role"] == "system":
                    langchain_messages.append(SystemMessage(content=message["content"]))
                elif message["role"] == "user":
                    langchain_messages.append(HumanMessage(content=message["content"]))
                elif message["role"] == "assistant":
                    langchain_messages.append(AIMessage(content=message["content"]))
            
            response = self.gemini.invoke(langchain_messages)
            return response.content
        except Exception as e:
            logger.warning(f"Error querying Gemini model: {e}")
            logger.warning("Using Claude as fallback for this query")
            return await self._use_claude_as_gemini_fallback(messages)
    
    async def _use_claude_as_gemini_fallback(self, messages: List[Dict[str, str]]) -> str:
        """Helper method to use Claude as a fallback for Gemini"""
        # Extract the user message content for Claude
        user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
        # Create a Claude-specific prompt 
        claude_messages = [
            {"role": "system", "content": "You are providing biomarker categorization as a fallback for Gemini. Please categorize biomarkers by health function."},
            {"role": "user", "content": f"Categorize these biomarkers (as a fallback for Gemini):\n\n{user_content}"}
        ]
        # Use Claude as fallback
        return await self.claude_query(claude_messages)
    
    async def analyze_cost_effectiveness(self) -> str:
        """
        Use OpenAI to analyze the cost-effectiveness of packages.
        
        Returns:
            Analysis of cost per biomarker for each package
        """
        products_json = json.dumps(self.products["products"], indent=2)
        
        messages = [
            {"role": "system", "content": self.openai_system_prompt},
            {"role": "user", "content": f"""
            Analyze the cost-effectiveness of these blood test packages.
            Calculate the price per biomarker for each package and rank them from most to least cost-effective.
            Also identify any packages that provide unique biomarkers not available in other packages.
            
            Product data:
            {products_json}
            """
            }
        ]
        
        response = await self.openai_query(messages)
        return response
    
    async def categorize_biomarkers(self) -> str:
        """
        Use Gemini to categorize biomarkers by health function.
        
        Returns:
            Categorized biomarker information
        """
        all_biomarkers = []
        
        # Collect all unique biomarkers from all products
        for product in self.products["products"]:
            if "biomarkers" in product:
                if isinstance(product["biomarkers"], list):
                    if len(product["biomarkers"]) > 0:
                        if isinstance(product["biomarkers"][0], dict):
                            # Handle categorized biomarkers
                            for category in product["biomarkers"]:
                                if "markers" in category:
                                    all_biomarkers.extend(category["markers"])
                        else:
                            # Handle flat list biomarkers
                            all_biomarkers.extend(product["biomarkers"])
        
        # Remove duplicates
        unique_biomarkers = list(set(all_biomarkers))
        biomarkers_json = json.dumps(unique_biomarkers, indent=2)
        
        messages = [
            {"role": "system", "content": self.gemini_system_prompt},
            {"role": "user", "content": f"""
            Categorize these biomarkers by health function (cardiovascular, metabolic, hormonal, etc.).
            Provide a brief explanation of what each biomarker measures and its significance.
            If possible, identify which biomarkers are most important for general health monitoring.
            
            Biomarkers:
            {biomarkers_json}
            """
            }
        ]
        
        response = await self.gemini_query(messages)
        return response
    
    async def recommend_packages(self, query: str) -> str:
        """
        Process a user query and provide package recommendations.
        
        Args:
            query: User query about blood test package selection
            
        Returns:
            Claude's recommendation based on inputs from all agents
        """
        try:
            # Get cost-effectiveness analysis from OpenAI
            logger.info("Getting cost-effectiveness analysis from OpenAI (or fallback)")
            cost_analysis = await self.analyze_cost_effectiveness()
            
            # Get biomarker categorization from Gemini
            logger.info("Getting biomarker categorization from Gemini (or fallback)")
            biomarker_categories = await self.categorize_biomarkers()
            
            # Combine all information and use Claude for final recommendation
            logger.info("Generating final recommendation with Claude")
            products_json = json.dumps(self.products["products"], indent=2)
            
            messages = [
                {"role": "system", "content": self.claude_system_prompt},
                {"role": "user", "content": f"""
                I need a recommendation for blood test packages based on this query:
                "{query}"
                
                Here is the data to consider:
                
                1. Available blood test packages:
                {products_json}
                
                2. Cost-effectiveness analysis:
                {cost_analysis}
                
                3. Biomarker categorization:
                {biomarker_categories}
                
                Based on all this information, what blood test package(s) would you recommend for this query?
                Explain your reasoning considering biomarker coverage, cost-effectiveness, and relevance to the query.
                """
                }
            ]
            
            response = await self.claude_query(messages)
            return response
            
        except Exception as e:
            logger.error(f"Error generating recommendation: {e}")
            # Fallback to basic Claude response if the multi-agent approach fails
            fallback_messages = [
                {"role": "system", "content": "You are an expert in blood test analysis. Answer directly based on available data."},
                {"role": "user", "content": f"""
                I need a recommendation for blood test packages based on this query:
                "{query}"
                
                Here are the available blood test packages:
                {json.dumps(self.products["products"], indent=2)}
                
                Provide a direct recommendation with basic reasoning.
                """}
            ]
            
            try:
                fallback_response = await self.claude_query(fallback_messages)
                return f"[Using simplified analysis due to an error with the multi-agent system]\n\n{fallback_response}"
            except Exception as fallback_error:
                logger.error(f"Critical error, even fallback failed: {fallback_error}")
                return f"Error processing your query: {e}\n\nPlease try again or simplify your query."

def initialize_claude(model_name="claude-3-7-sonnet-20250219"):
    """
    Initialize the Claude model
    
    Args:
        model_name: The name of the Claude model to use
        
    Returns:
        Tuple of (model_instance, success_flag)
    """
    try:
        model = ChatAnthropic(model=model_name)
        log_model_init("Claude", model_name)
        return model, True
    except Exception as e:
        logger.error(f"Failed to initialize Claude model: {e}")
        log_model_init("Claude", model_name, success=False)
        return None, False

def initialize_openai(model_name="gpt-3.5-turbo-0125"):
    """
    Initialize the OpenAI model if API key is available
    
    Args:
        model_name: The name of the OpenAI model to use
        
    Returns:
        Tuple of (model_instance, success_flag)
    """
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("OpenAI API key not found - related features will use Claude as fallback")
        return None, False
        
    try:
        model = ChatOpenAI(model=model_name)
        log_model_init("OpenAI", model_name)
        return model, True
    except Exception as e:
        logger.warning(f"Failed to initialize OpenAI model: {e}")
        log_model_init("OpenAI", model_name, success=False)
        return None, False

def initialize_gemini(model_name="gemini-1.5-flash-latest"):
    """
    Initialize the Gemini model if API key is available
    
    Args:
        model_name: The name of the Gemini model to use
        
    Returns:
        Tuple of (model_instance, success_flag)
    """
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        logger.warning("Gemini API key not found - related features will use Claude as fallback")
        return None, False
        
    try:
        # Set the API key directly in the environment var that langchain-google expects
        os.environ["GOOGLE_API_KEY"] = gemini_api_key
        model = ChatGoogleGenerativeAI(model=model_name, google_api_key=gemini_api_key)
        log_model_init("Gemini", model_name)
        return model, True
    except Exception as e:
        logger.warning(f"Failed to initialize Gemini model: {e}")
        log_model_init("Gemini", model_name, success=False)
        return None, False

async def check_model_availability():
    """
    Check if the models are available and properly configured by testing realistic use cases
    
    Returns:
        Dictionary with model statuses
    """
    log_section("Checking Model Availability")
    results = {
        "claude": {"available": False, "message": "Not tested"},
        "openai": {"available": False, "message": "Not tested"},
        "gemini": {"available": False, "message": "Not tested"}
    }
    
    # Initialize models
    claude, claude_init_success = initialize_claude()
    openai, openai_init_success = initialize_openai()
    gemini, gemini_init_success = initialize_gemini()
    
    # Test Claude with a realistic test case
    if claude_init_success:
        try:
            logger.info("Testing Claude model with a realistic example...")
            test_message = "What are the key biomarkers for cardiovascular health?"
            
            claude_messages = [
                SystemMessage(content="You are a medical expert. Keep your answer brief."),
                HumanMessage(content=test_message)
            ]
                
            response = await claude.ainvoke(claude_messages)
            
            if response and response.content and len(response.content) > 10:
                results["claude"]["available"] = True
                results["claude"]["message"] = "Connected and operational with realistic test"
                logger.info("✓ Claude model test successful")
            else:
                results["claude"]["message"] = "Connected but returned insufficient response"
                logger.warning("⚠ Claude returned unexpected response")
                
        except Exception as e:
            results["claude"]["message"] = f"Error: {str(e)}"
            logger.error(f"✗ Claude model test failed: {e}")
    else:
        results["claude"]["message"] = "Initialization failed"
        
    # Test OpenAI with a realistic numerical analysis task
    if openai_init_success:
        try:
            logger.info("Testing OpenAI model with a realistic example...")
            test_data = {
                "products": [
                    {"name": "Test Kit A", "price": 99, "biomarkers": ["CRP", "Cholesterol", "Glucose"]},
                    {"name": "Test Kit B", "price": 149, "biomarkers": ["CRP", "Cholesterol", "Glucose", "HbA1c", "Iron"]}
                ]
            }
            
            test_message = f"Calculate the cost per biomarker for these test kits: {json.dumps(test_data)}"
            
            openai_messages = [
                SystemMessage(content="You are a numerical analysis expert."),
                HumanMessage(content=test_message)
            ]
                
            response = await openai.ainvoke(openai_messages)
            
            if response and response.content and len(response.content) > 10:
                results["openai"]["available"] = True
                results["openai"]["message"] = "Connected and operational with realistic test"
                logger.info("✓ OpenAI model test successful")
            else:
                results["openai"]["message"] = "Connected but returned insufficient response"
                logger.warning("⚠ OpenAI returned unexpected response")
                
        except Exception as e:
            results["openai"]["message"] = f"Error: {str(e)}"
            logger.warning(f"✗ OpenAI model test failed: {e}")
    else:
        results["openai"]["message"] = "Not configured or initialization failed"
    
    # Test Gemini with a realistic biomarker categorization task
    if gemini_init_success:
        try:
            logger.info("Testing Gemini model with a realistic example...")
            test_biomarkers = ["CRP", "Cholesterol", "Glucose", "HbA1c", "Iron", "Vitamin D", "Testosterone"]
            
            test_message = f"Categorize these biomarkers by health function: {', '.join(test_biomarkers)}"
            
            gemini_messages = [
                SystemMessage(content="You are a biomarker expert."),
                HumanMessage(content=test_message)
            ]
            
            response = await gemini.ainvoke(gemini_messages)
            
            if response and response.content and len(response.content) > 10:
                results["gemini"]["available"] = True
                results["gemini"]["message"] = "Connected and operational with realistic test"
                logger.info("✓ Gemini model test successful")
            else:
                results["gemini"]["message"] = "Connected but returned insufficient response"
                logger.warning("⚠ Gemini returned unexpected response")
                
        except Exception as e:
            results["gemini"]["message"] = f"Error: {str(e)}"
            logger.warning(f"✗ Gemini model test failed: {e}")
    else:
        results["gemini"]["message"] = "Not configured or initialization failed"
        
    # Print summary
    logger.info("Model availability summary:")
    logger.info(f"Claude: {'✓' if results['claude']['available'] else '✗'} ({results['claude']['message']})")
    logger.info(f"OpenAI: {'✓' if results['openai']['available'] else '✗'} ({results['openai']['message']})")
    logger.info(f"Gemini: {'✓' if results['gemini']['available'] else '✗'} ({results['gemini']['message']})")
    
    if not results["claude"]["available"]:
        logger.error("Claude is required and not available. Application cannot run.")
        raise RuntimeError("Claude is required and not available.")
        
    return results

# Example usage
if __name__ == "__main__":
    import asyncio
    
    async def main():
        try:
            # Check model availability before proceeding
            log_section("Starting Blood Test Kit Advisor")
            logger.info("Checking model availability...")
            
            # First ensure models are available
            model_status = await check_model_availability()
            
            # If we get here, Claude is available (since the function would raise an error otherwise)
            analyzer = BloodTestKitAdvisor()
            
            # Example query
            query = "Which blood test package is best for monitoring cardiovascular health?"
            
            response = await analyzer.recommend_packages(query)
            log_section("Recommendation Results")
            
            # Format the output for terminal readability - add line wrapping and spacing
            formatted_lines = []
            for paragraph in response.split('\n\n'):
                formatted_lines.append(paragraph)
                formatted_lines.append('')  # Add spacing between paragraphs
                
            print('\n'.join(formatted_lines))
            
        except Exception as e:
            logger.critical(f"Failed to run the advisor: {e}")
            print(f"\nERROR: {e}")
            print("Please check the logs for more details.")
    
    asyncio.run(main())