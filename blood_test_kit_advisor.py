import os
import json
from pathlib import Path
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from typing import List, Dict, Any # Removed Optional, Union
import logging
from colorlog import ColoredFormatter
import redis_cache
import argparse
import re
import sys
# import redis # Removed unused import

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
    logger.info("%s %s %s", char * padding, title, char * padding)
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
    
    logger.info("┌─ %s Model Initialization ───────────────────────", model_type)
    logger.info("│ Status: %s %s", status, status_msg)
    logger.info("│ Model:  %s", model_name)
    logger.info("└───────────────────────────────────────────────────────")

class BloodTestKitAdvisor:
    """
    Multi-agent system for recommending blood test kits using Claude, OpenAI, and Gemini.
    """
    
    def __init__(self, data_path: str = "data/products.json", use_cache: bool = True, clear_cache_on_start: bool = True):
        """
        Initialize the advisor with API clients and load dataset.
        
        Args:
            data_path: Path to the JSON file containing blood test products
            use_cache: Whether to use Redis caching
            clear_cache_on_start: Whether to clear cache on initialization
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
        
        # Initialize Redis cache
        self.use_cache = use_cache
        if self.use_cache:
            cache_success = redis_cache.initialize_redis()
            if cache_success:
                logger.info("Redis cache initialized successfully")
                if clear_cache_on_start:
                    redis_cache.invalidate_cache()
                    logger.info("Redis cache cleared on startup (default behavior)")
            else:
                logger.warning("Redis cache initialization failed, continuing without caching")
                self.use_cache = False
        
        # Load dataset
        self.data_path = data_path
        self.products = self._load_products()
        logger.info("Loaded dataset with %d products", len(self.products.get('products', [])))
        
        # Set up system prompts for each agent
        self.claude_system_prompt = """
        You are an expert medical advisor specializing in blood test analysis and recommendations.
        Your role is to help users understand which blood test packages would be most appropriate for 
        their specific health needs, taking into consideration biomarker coverage, cost-effectiveness, 
        and scientific relevance.
        
        When providing recommendations:
        1. Consider the user's specific health concerns or goals if provided
        2. Evaluate cost-effectiveness, recognizing that not all biomarkers have equal importance despite the calculated cost_per_biomarker
        3. Consider comprehensive coverage of important health markers, prioritizing clinically significant biomarkers
        4. Provide clear, evidence-based explanations for your recommendations
        
        You can delegate numerical analysis tasks to OpenAI and biomarker categorization to Gemini.
        Your final recommendations should synthesize all available information.
        """
        
        self.openai_system_prompt = """
        You are a data analysis expert specializing in numerical evaluation of blood test packages.
        Your role is to analyze blood test packages quantitatively and provide structured comparisons.
        
        When performing analysis:
        1. Use the provided cost_per_biomarker to analyze cost-effectiveness
        2. Note that raw cost_per_biomarker may be misleading as not all biomarkers have equal importance
        3. Identify overlaps and unique biomarkers between packages
        4. Find optimal combinations for complete biomarker coverage
        5. Present data in a structured, quantitative format
        
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

        self.openai_synthesis_prompt = """
        You are an AI assistant skilled at synthesizing complex information into clear, user-friendly recommendations.
        Your role is to take analysis from different sources (biomarker weighting, cost analysis, biomarker categorization)
        and combine it with raw product data and a user's query to generate a final, coherent recommendation for blood test packages.

        When generating the final recommendation:
        1.  Address the user's specific query directly.
        2.  Integrate insights from the weighted cost-effectiveness analysis (which considers biomarker importance).
        3.  Incorporate the biomarker categorizations provided to explain the relevance of tests.
        4.  Refer to the specific product data (names, prices, included biomarkers) as needed.
        5.  Provide clear reasoning for your recommendation(s), explaining *why* certain packages are suitable based on the combined analysis.
        6.  Present the final output in a helpful, easy-to-understand format for the end-user. Avoid overly technical jargon where possible, but maintain accuracy.
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
            logger.info("%s: %s (%s)", key, status, masked_value if value else 'Not provided')
        
        # Check which optional keys are missing
        missing_optional = []
        if not os.getenv("OPENAI_API_KEY"):
            missing_optional.append("OPENAI_API_KEY")
        if not os.getenv("GEMINI_API_KEY"):
            missing_optional.append("GEMINI_API_KEY")
            
        if missing_optional:
            logger.warning("Missing optional API keys: %s", ', '.join(missing_optional))
            logger.warning("Claude will be used as a fallback for these capabilities")
            logger.warning("For best results, consider adding these keys to your .env file")
    
    def _load_products(self) -> Dict[str, Any]:
        """Load product data from JSON file."""
        data_file = Path(self.data_path)
        
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {self.data_path}")
        
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logger.info("Loaded %d products from %s", len(data['products']), self.data_path)
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
            Gemini's response text.

        Raises:
            ValueError: If the Gemini client is not initialized.
            Exception: If the API call fails for other reasons.
        """
        # Check if Gemini is initialized
        if not self.gemini:
            logger.error("Gemini client not initialized. Cannot perform Gemini query.")
            raise ValueError("Gemini client is not available or not initialized.")

        # Exceptions during invoke will now propagate upwards
        langchain_messages = []
        for message in messages:
            if message["role"] == "system":
                langchain_messages.append(SystemMessage(content=message["content"]))
            elif message["role"] == "user":
                langchain_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                langchain_messages.append(AIMessage(content=message["content"]))

        logger.debug("Sending query to Gemini model...") # Add some logging
        response = self.gemini.invoke(langchain_messages)
        logger.debug("Received response from Gemini.")
        return response.content

    # Removed _use_claude_as_gemini_fallback method as it's no longer needed
    
    async def analyze_cost_effectiveness(self) -> str:
        """
        Use OpenAI to analyze the cost-effectiveness of packages.
        Uses Redis cache when available.
        
        Returns:
            Analysis of cost per biomarker for each package
        """
        if not self.use_cache:
            # Original implementation without caching
            return await self._analyze_cost_effectiveness_uncached()
        
        # Use cached_or_compute to handle caching
        async def compute_analysis():
            return await self._analyze_cost_effectiveness_uncached()
        
        return await redis_cache.cached_or_compute(
            "cost_analysis", 
            self.products["products"], 
            compute_analysis
        )
    
    async def _analyze_cost_effectiveness_uncached(self) -> str:
        """Original implementation without caching"""
        products_json = json.dumps(self.products["products"], indent=2)
        
        messages = [
            {"role": "system", "content": self.openai_system_prompt},
            {"role": "user", "content": f"""
            Analyze the cost-effectiveness of these blood test packages.
            Use the pre-calculated cost_per_biomarker value to rank packages from most to least cost-effective.
            Also identify any packages that provide unique biomarkers not available in other packages.
            
            Product data:
            {products_json}
            """
            }
        ]
        
        response = await self.openai_query(messages)
        return response
    
    async def analyze_cost_effectiveness_structured(self) -> dict:
        """
        Use OpenAI to analyze the cost-effectiveness of packages with structured JSON output.
        Uses Redis cache when available.
        
        Returns:
            JSON structured analysis of cost per biomarker for each package
        """
        if not self.use_cache:
            # Implementation without caching
            return await self._analyze_cost_effectiveness_structured_uncached()
        
        # Use cached_or_compute to handle caching
        async def compute_analysis():
            result = await self._analyze_cost_effectiveness_structured_uncached()
            # Convert to JSON string for caching
            return json.dumps(result)
        
        result = await redis_cache.cached_or_compute(
            "cost_analysis_structured", 
            self.products["products"], 
            compute_analysis
        )
        
        # Convert JSON string back to dict
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                logger.warning("Error parsing cached JSON result, recomputing")
                return await self._analyze_cost_effectiveness_structured_uncached()
        
        return result
    
    async def _analyze_cost_effectiveness_structured_uncached(self) -> dict:
        """Implementation for structured JSON output without caching"""
        products_json = json.dumps(self.products["products"], indent=2)
        
        messages = [
            {"role": "system", "content": self.openai_system_prompt + """
            IMPORTANT: You must respond with a valid JSON object containing your analysis.
            The JSON structure should be:
            {
                "cost_effectiveness_ranking": [
                    {"package_name": "Name", "cost_per_biomarker": 0.00, "ranking": 1, "notes": "..."}
                ],
                "unique_biomarkers": [
                    {"package_name": "Name", "unique_biomarkers": ["marker1", "marker2"]}
                ],
                "summary": "Brief text summary of the analysis"
            }
            """
            },
            {"role": "user", "content": f"""
            Analyze the cost-effectiveness of these blood test packages.
            Use the pre-calculated cost_per_biomarker value to rank packages from most to least cost-effective.
            Also identify any packages that provide unique biomarkers not available in other packages.
            
            Product data:
            {products_json}
            
            Respond ONLY with a valid JSON object structured exactly as specified in the system prompt.
            """
            }
        ]
        
        response = await self.openai_query(messages)
        
        # Parse the JSON response with more robust error handling
        try:
            # Extract JSON if it's wrapped in markdown code blocks
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                json_str = response.strip()
            
            # Fix common JSON formatting issues
            json_str = self._fix_json_formatting(json_str)
            
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse OpenAI response as JSON: %s", e)
            logger.warning("Falling back to unstructured response")
            # Return a structured dict with the text in the summary field as fallback
            return {
                "cost_effectiveness_ranking": [],
                "unique_biomarkers": [],
                "summary": response,
                "parsing_error": str(e)
            }
    
    def _fix_json_formatting(self, json_str: str) -> str:
        """Fix common JSON formatting issues"""
        # Remove trailing commas before closing brackets or braces
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*\]', ']', json_str)
        
        # Fix any missing quotes around keys (basic fix)
        json_str = re.sub(r'([{,]\s*)(\w+)(\s*:)', r'\1"\2"\3', json_str)
        
        # More aggressive fix: ensure all keys are properly quoted
        # This will handle keys with special characters like colons, spaces, parentheses
        lines = json_str.split('\n')
        fixed_lines = []
        
        for line in lines:
            # Skip lines that don't look like key-value pairs
            if ':' not in line:
                fixed_lines.append(line)
                continue
            
            # Check if this looks like a key-value pair with an unquoted or improperly quoted key
            key_pattern = re.search(r'^\s*"?([^"]*?)"?\s*:', line)
            if key_pattern:
                key = key_pattern.group(1)
                # Replace the original key with a properly quoted version
                fixed_line = line.replace(f'{key}:', f'"{key}":')
                # Make sure we didn't double-quote
                fixed_line = fixed_line.replace('""', '"')
                fixed_lines.append(fixed_line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    async def categorize_biomarkers(self) -> str:
        """
        Use Gemini to categorize biomarkers by health function.
        Uses Redis cache when available.
        
        Returns:
            Categorized biomarker information
        """
        if not self.use_cache:
            # Original implementation without caching
            return await self._categorize_biomarkers_uncached()
        
        # Extract all unique biomarkers
        all_biomarkers = self._extract_unique_biomarkers()
        
        # Use cached_or_compute to handle caching
        async def compute_categorization():
            return await self._categorize_biomarkers_with_biomarkers(all_biomarkers)
        
        return await redis_cache.cached_or_compute(
            "biomarker_categories", 
            all_biomarkers, 
            compute_categorization
        )
    
    def _extract_unique_biomarkers(self) -> list:
        """Extract all unique biomarkers from products"""
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
        return list(set(all_biomarkers))
    
    async def _categorize_biomarkers_with_biomarkers(self, unique_biomarkers: list) -> str:
        """Categorize the given list of biomarkers"""
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
    
    async def _categorize_biomarkers_uncached(self) -> str:
        """Original implementation without caching"""
        unique_biomarkers = self._extract_unique_biomarkers()
        return await self._categorize_biomarkers_with_biomarkers(unique_biomarkers)
    
    async def categorize_biomarkers_structured(self) -> dict:
        """
        Use Gemini to categorize biomarkers by health function with structured JSON output.
        Uses Redis cache when available.
        
        Returns:
            JSON structured categorization of biomarkers
        """
        if not self.use_cache:
            # Implementation without caching
            return await self._categorize_biomarkers_structured_uncached()
        
        # Extract all unique biomarkers
        all_biomarkers = self._extract_unique_biomarkers()
        
        # Use cached_or_compute to handle caching
        async def compute_categorization():
            result = await self._categorize_biomarkers_structured_with_biomarkers(all_biomarkers)
            # Convert to JSON string for caching
            return json.dumps(result)
        
        result = await redis_cache.cached_or_compute(
            "biomarker_categories_structured", 
            all_biomarkers, 
            compute_categorization
        )
        
        # Convert JSON string back to dict
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                logger.warning("Error parsing cached JSON result, recomputing")
                return await self._categorize_biomarkers_structured_with_biomarkers(all_biomarkers)
        
        return result
    
    async def _categorize_biomarkers_structured_uncached(self) -> dict:
        """Implementation for structured JSON categorization without caching"""
        unique_biomarkers = self._extract_unique_biomarkers()
        return await self._categorize_biomarkers_structured_with_biomarkers(unique_biomarkers)
    
    async def _categorize_biomarkers_structured_with_biomarkers(self, unique_biomarkers: list) -> dict:
        """Categorize the given list of biomarkers with structured JSON output"""
        biomarkers_json = json.dumps(unique_biomarkers, indent=2)
        
        messages = [
            {"role": "system", "content": self.gemini_system_prompt + """
            IMPORTANT: You must respond with a valid JSON object containing your categorization.
            The JSON structure should be:
            {
                "categories": [
                    {
                        "name": "Category Name",
                        "description": "Brief description of this health category",
                        "biomarkers": [
                            {
                                "name": "Biomarker Name",
                                "description": "What this biomarker measures",
                                "significance": "Clinical significance of this biomarker",
                                "importance_level": "high/medium/low"
                            }
                        ]
                    }
                ],
                "important_general_health_markers": ["marker1", "marker2"],
                "summary": "Brief text summary of the categorization"
            }
            """
            },
            {"role": "user", "content": f"""
            Categorize these biomarkers by health function (cardiovascular, metabolic, hormonal, etc.).
            Provide a brief explanation of what each biomarker measures and its significance.
            Identify which biomarkers are most important for general health monitoring.
            
            Biomarkers:
            {biomarkers_json}
            
            Respond ONLY with a valid JSON object structured exactly as specified in the system prompt.
            """
            }
        ]

        try:
            # Attempt to get categorization from Gemini
            logger.info("Querying Gemini for structured biomarker categorization...")
            response = await self.gemini_query(messages)
            logger.info("Received response from Gemini.")

            # Attempt to parse the JSON response
            try:
                # Extract JSON if it's wrapped in markdown code blocks
                if "```json" in response:
                    json_str = response.split("```json")[1].split("```")[0].strip()
                elif "```" in response:
                    json_str = response.split("```")[1].split("```")[0].strip()
                else:
                    json_str = response.strip()

                # Fix common JSON formatting issues
                json_str = self._fix_json_formatting(json_str)

                logger.debug("Attempting to parse Gemini JSON response: %s...", json_str[:200])
                parsed_response = json.loads(json_str)
                logger.info("Successfully parsed Gemini JSON response.")
                return parsed_response

            except json.JSONDecodeError as e:
                logger.error("Failed to parse essential Gemini response as JSON: %s", e)
                logger.error("Raw Gemini response snippet: %s...", response[:200])
                # Raise an error because categorization is essential and failed
                raise ValueError("Failed to parse critical biomarker categorization from Gemini.") from e

        except Exception as e:
            # Catch errors from gemini_query (e.g., API errors, client not initialized)
            logger.error("Failed to get biomarker categorization from Gemini: %s", e)
            # Re-raise the exception to stop the process
            raise RuntimeError("Essential biomarker categorization step failed.") from e
    
    def clear_cache(self, cache_type: str = None):
        """
        Clear the Redis cache.
        
        Args:
            cache_type: Type of cache to clear ("cost_analysis", "biomarker_categories") 
                       or None to clear all
        """
        if not self.use_cache:
            logger.warning("Cache not enabled, nothing to clear")
            return
            
        if cache_type:
            redis_cache.invalidate_cache(cache_type)
            logger.info("Cleared %s cache", cache_type)
        else:
            redis_cache.invalidate_cache()
            logger.info("Cleared all cache entries")
    
    async def analyze_biomarker_weights(self, query: str = None) -> dict:
        """
        Use Claude to assign weights to biomarkers based on health importance and query relevance.
        
        Args:
            query: User's health query or concern (optional)
            
        Returns:
            Dictionary mapping biomarkers to their importance weights
        """
        unique_biomarkers = self._extract_unique_biomarkers()
        biomarkers_json = json.dumps(unique_biomarkers, indent=2)
        
        prompt_context = ""
        if query:
            prompt_context = f"""
            User query: "{query}"
            
            Consider this specific health concern when weighting biomarkers.
            """
        
        messages = [
            {"role": "system", "content": """
             You are a medical expert specializing in laboratory diagnostics.
             Assign importance weights to blood test biomarkers on a scale of 1-10 where:
             
             10 = Critical biomarker that provides fundamental health insights
             7-9 = Very important biomarker for general health assessment
             4-6 = Moderately important biomarker in specific contexts
             1-3 = Complementary biomarker with limited standalone value
             
             Consider factors like:
             - Clinical significance in disease detection and monitoring
             - Relevance to common health conditions
             - Diagnostic specificity and sensitivity
             - Relevance to the user's specific health query if provided
             
             CRITICALLY IMPORTANT: Your response MUST be ONLY a valid JSON object, formatted as:
             {
                 "Biomarker Name": 9,
                 "Another Biomarker": 8
             }
             
             IMPORTANT JSON FORMATTING RULES:
             1. Every biomarker name MUST be enclosed in double quotes
             2. Use only numbers (1-10) as values, not strings
             3. No trailing commas at the end of lists or objects
             4. No comments or extra text
             """
            },
            {"role": "user", "content": f"""
             Assign importance weights (1-10) to these biomarkers:
             {biomarkers_json}
             
             {prompt_context}
             
             Return ONLY valid JSON. Every key must be in double quotes. Every value must be a number.
             
             Important: Some biomarker names contain special characters (colons, parentheses, etc.).
             Make sure ALL biomarker names are properly enclosed in double quotes in your JSON response.
             """
            }
        ]
        
        response = await self.claude_query(messages)
        
        # Extract JSON from response with improved robustness
        try:
            # Remove any non-JSON content
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                # Try to find where the JSON object starts and ends
                start_idx = response.find('{')
                end_idx = response.rfind('}') + 1
                if start_idx >= 0 and end_idx > start_idx:
                    json_str = response[start_idx:end_idx]
                else:
                    json_str = response.strip()
            
            # Apply more extensive JSON formatting fixes
            json_str = self._fix_json_formatting(json_str)
            
            # For debugging
            logger.debug("Attempting to parse fixed JSON: %s...", json_str[:200])
            
            # Try parsing the fixed JSON
            try:
                weights = json.loads(json_str)
            except json.JSONDecodeError:
                # Last resort: manually build the dictionary
                weights = {}
                pattern = r'"([^"]+)":\s*(\d+)'
                matches = re.findall(pattern, json_str)
                for biomarker, weight in matches:
                    try:
                        weights[biomarker] = int(weight)
                    except ValueError:
                        weights[biomarker] = 5
            
            if not weights:
                raise json.JSONDecodeError("Failed to extract key-value pairs", json_str, 0)
        
            # Ensure all weights are numeric
            for key, value in list(weights.items()):
                if not isinstance(value, (int, float)) or value < 1 or value > 10:
                    logger.warning("Invalid weight value for %s: %s, defaulting to 5", key, value)
                    weights[key] = 5
        
            logger.info("Successfully parsed weights for %d biomarkers", len(weights))
            
            return weights
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse biomarker weights as JSON: %s", e)
            logger.warning("Raw response: %s...", response[:200])
            
            # Create default weights as fallback
            default_weights = {biomarker: 5 for biomarker in unique_biomarkers}
            logger.info("Using default weight (5) for all %d biomarkers", len(default_weights))
            return default_weights
    
    async def analyze_weighted_cost_effectiveness(self, query: str = None) -> dict:
        """
        Calculate weighted cost-effectiveness using biomarker importance weights.
        
        Args:
            query: User's health query to consider when weighting (optional)
            
        Returns:
            Dictionary with weighted cost-effectiveness analysis
        """
        # Get biomarker weights
        biomarker_weights = await self.analyze_biomarker_weights(query)
        
        # Default weight for biomarkers not in the weights dictionary
        default_weight = 5
        
        products_data = []
        for product in self.products["products"]:
            product_biomarkers = []
            if "biomarkers" in product:
                if isinstance(product["biomarkers"], list):
                    if len(product["biomarkers"]) > 0:
                        if isinstance(product["biomarkers"][0], dict):
                            # Handle categorized biomarkers
                            for category in product["biomarkers"]:
                                if "markers" in category:
                                    product_biomarkers.extend(category["markers"])
                        else:
                            # Handle flat list biomarkers
                            product_biomarkers = product["biomarkers"]
            
            # Calculate weighted biomarker value
            total_weight = sum(biomarker_weights.get(marker, default_weight) for marker in product_biomarkers)
            
            # Calculate weighted cost per unit of importance
            price = product.get("price", 0)
            weighted_cost_effectiveness = price / total_weight if total_weight > 0 else float('inf')
            
            products_data.append({
                "name": product.get("name", "Unknown"),
                "price": price,
                "biomarker_count": len(product_biomarkers),
                "total_importance_weight": total_weight,
                "weighted_cost_per_importance_unit": round(weighted_cost_effectiveness, 2),
                "raw_cost_per_biomarker": round(price / len(product_biomarkers) if product_biomarkers else 0, 2),
                "biomarkers": product_biomarkers
            })
        
        # Sort by weighted cost-effectiveness
        products_data.sort(key=lambda x: x["weighted_cost_per_importance_unit"])
        
        return {
            "products": products_data,
            "weights": biomarker_weights,
            "query_context": query
        }

    async def recommend_packages(self, query: str, use_structured_output: bool = True) -> str:
        """
        Process a user query and provide package recommendations.
        
        Args:
            query: User query about blood test package selection
            use_structured_output: Whether to use structured JSON outputs from Gemini and OpenAI
            
        Returns:
            Claude's recommendation based on inputs from all agents
        """
        try:
            # 1. Get weighted cost-effectiveness analysis
            logger.info("Generating weighted cost-effectiveness analysis based on query")
            weighted_analysis = await self.analyze_weighted_cost_effectiveness(query)
            weighted_analysis_json = json.dumps(weighted_analysis, indent=2) # Prepare JSON early

            # 2. Get biomarker categorization from Gemini (with specific error handling)
            biomarker_categories_data = None
            try:
                if use_structured_output:
                    logger.info("Getting structured biomarker categorization from Gemini")
                    biomarker_categories_data = await self.categorize_biomarkers_structured()
                    biomarker_categories_json = json.dumps(biomarker_categories_data, indent=2)
                else:
                    logger.info("Getting unstructured biomarker categorization from Gemini")
                    biomarker_categories_data = await self.categorize_biomarkers()
                    biomarker_categories_text = biomarker_categories_data # Keep as string
            except Exception as gemini_error:
                logger.error("Gemini categorization step failed: %s", gemini_error, exc_info=True)
                # Raise a specific error to halt the process as per the revised plan
                raise RuntimeError("Gemini categorization failed, cannot proceed with recommendation.") from gemini_error

            # 3. Prepare data for final synthesis
            products_json = json.dumps(self.products["products"], indent=2)
            
            # Determine categorization format for the prompt
            categorization_input = biomarker_categories_json if use_structured_output else biomarker_categories_text

            # 4. Final Synthesis using OpenAI (if available)
            if not self.openai: # Corrected attribute name
                logger.error("OpenAI client not available. Cannot perform final synthesis.")
                # Decide how to handle this - maybe fallback to Claude or return error?
                # For now, let's return an error consistent with halting on critical failures.
                return "Error: OpenAI client not configured. Cannot generate recommendation."

            logger.info("Generating final recommendation with OpenAI using structured/unstructured inputs")
            
            # Construct messages for OpenAI
            messages = [
                {"role": "system", "content": self.openai_synthesis_prompt}, # Use OpenAI specific prompt
                {"role": "user", "content": f"""
                I need a recommendation for blood test packages based on this query:
                "{query}"

                Here is the data to consider:

                1. Available blood test packages:
                {products_json}

                2. Weighted cost-effectiveness analysis (considers biomarker importance):
                {weighted_analysis_json}

                3. Biomarker categorization:
                {categorization_input}

                Based on all this information, what blood test package(s) would you recommend for this query?
                Explain your reasoning considering biomarker coverage, weighted cost-effectiveness, and relevance to the query based on the provided categorization.
                """
                }
            ]

            # Call OpenAI
            response = await self.openai_query(messages) # Use OpenAI query method
            return response

        except RuntimeError as e:
             # Catch the specific error from the Gemini step
            logger.error("Halting recommendation due to critical error: %s", e)
            return f"Error processing your query: {e}\n\nPlease check the logs or try again later."

        except Exception as e:
            logger.error("An unexpected error occurred during recommendation generation: %s", e, exc_info=True)
            # Fallback to basic Claude response ONLY if the error was NOT the Gemini failure
            logger.warning("Attempting fallback recommendation using Claude due to unexpected error.")
            fallback_messages = [
                {"role": "system", "content": "You are an expert in blood test analysis. Answer directly based on available data."},
                {"role": "user", "content": f"""
                An error occurred during the detailed analysis. Please provide a basic recommendation for blood test packages based on this query:
                "{query}"

                Here are the available blood test packages:
                {json.dumps(self.products["products"], indent=2)}

                Provide a direct recommendation with basic reasoning, acknowledging the limited analysis.
                """}
            ]

            try:
                if self.claude: # Corrected attribute name, Check if Claude client is available for fallback
                    fallback_response = await self.claude_query(fallback_messages)
                    return f"[Using simplified analysis due to an unexpected error in the multi-agent system]\n\n{fallback_response}"
                else:
                    logger.error("Claude client not available for fallback.")
                    return f"Error processing your query: {e}\n\nFallback mechanism also unavailable. Please try again or simplify your query."
            except Exception as fallback_error:
                logger.error("Critical error, even fallback failed: %s", fallback_error, exc_info=True)
                # Use the original error 'e' in the final message for clarity on the root cause
                return f"Error processing your query: {e}\n\nFallback attempt also failed. Please try again or simplify your query."

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
        logger.error("Failed to initialize Claude model: %s", e)
        log_model_init("Claude", model_name, success=False)
        return None, False

def initialize_openai(model_name="o3-2025-04-16"):
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
        logger.warning("Failed to initialize OpenAI model: %s", e)
        log_model_init("OpenAI", model_name, success=False)
        return None, False

def initialize_gemini(model_name="gemini-2.5-pro-preview-03-25"):
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
        logger.warning("Failed to initialize Gemini model: %s", e)
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
            logger.error("✗ Claude model test failed: %s", e)
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
            logger.warning("✗ OpenAI model test failed: %s", e)
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
            logger.warning("✗ Gemini model test failed: %s", e)
    else:
        results["gemini"]["message"] = "Not configured or initialization failed"
        
    # Print summary
    logger.info("Model availability summary:")
    logger.info("Claude: %s (%s)", '✓' if results['claude']['available'] else '✗', results['claude']['message'])
    logger.info("OpenAI: %s (%s)", '✓' if results['openai']['available'] else '✗', results['openai']['message'])
    logger.info("Gemini: %s (%s)", '✓' if results['gemini']['available'] else '✗', results['gemini']['message'])
    
    if not results["claude"]["available"]:
        logger.error("Claude is required and not available. Application cannot run.")
        raise RuntimeError("Claude is required and not available.")
        
    return results

def check_redis_connection():
    """Check if Redis connection is working properly"""
    try:
        # import redis_cache # Removed redundant import
        is_connected = redis_cache.test_connection()
        if is_connected:
            logger.info("Redis connection test: SUCCESS")
            return True
        else:
            logger.warning("Redis connection test: FAILED - Redis responded but connection test failed")
            return False
    except Exception as e:
        logger.error("Redis connection test: ERROR - %s", str(e))
        return False

# Example usage
if __name__ == "__main__":
    async def main():
        try:
            # Parse command-line arguments
            parser = argparse.ArgumentParser(description='Blood Test Kit Advisor')
            parser.add_argument('--no-cache', action='store_true', 
                              help='Disable Redis caching (enabled by default)')
            parser.add_argument('--preserve-cache', action='store_true', 
                              help='Preserve Redis cache on startup (cleared by default)')
            parser.add_argument('--query', type=str, 
                              help='Query to run (optional)', default=None)
            parser.add_argument('--unstructured', action='store_true',
                              help='Use unstructured output from OpenAI and Gemini (structured by default)')
            parser.add_argument('--test-redis', action='store_true',
                              help='Test Redis connection and exit')
            args = parser.parse_args()
            
            # Check model availability before proceeding
            log_section("Starting Blood Test Kit Advisor")
            logger.info("Checking model availability...")
            
            # First ensure models are available
            await check_model_availability() # Removed assignment to unused variable
            
            # Initialize the analyzer with cache settings
            # Redis is enabled by default and cleared by default
            analyzer = BloodTestKitAdvisor(
                use_cache=not args.no_cache,
                clear_cache_on_start=not args.preserve_cache
            )
            
            # Use provided query or default example
            query = args.query if args.query else "Which blood test package is best for monitoring cardiovascular health?"
            
            # Set whether to use structured output (structured by default)
            use_structured = not args.unstructured
            
            # Log whether using structured or unstructured output
            logger.info("%s output mode for agent communication", "Structured" if use_structured else "Unstructured")
            
            response = await analyzer.recommend_packages(query, use_structured_output=use_structured)
            log_section("Recommendation Results")
            
            # Format the output for terminal readability
            formatted_lines = []
            for paragraph in response.split('\n\n'):
                formatted_lines.append(paragraph)
                formatted_lines.append('')  # Add spacing between paragraphs
                
            print('\n'.join(formatted_lines))
            
            if args.test_redis:
                log_section("Testing Redis Connection")
                is_connected = check_redis_connection()
                if is_connected:
                    # Get more Redis details
                    info = redis_cache.get_redis_info()
                    print(f"Redis server version: {info.get('redis_version', 'unknown')}")
                    print(f"Connected clients: {info.get('connected_clients', 'unknown')}")
                    print(f"Used memory: {info.get('used_memory_human', 'unknown')}")
                    print(f"Uptime: {info.get('uptime_in_days', 'unknown')} days")
                    # Cache stats if available
                    print(f"Cache keys: {redis_cache.get_cache_stats().get('total_keys', 'unknown')}")
                else:
                    print("Redis connection failed. Please check your Redis server.")
                sys.exit(0)
            
        except Exception as e:
            logger.critical("Failed to run the advisor: %s", e)
            print(f"\nERROR: {e}")
            print("Please check the logs for more details.")
    
    import asyncio
    asyncio.run(main())