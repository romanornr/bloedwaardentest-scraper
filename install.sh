#!/bin/bash
set -e

echo "Blood Test Analyzer with AI Agents - Installation Script"
echo "-------------------------------------------------------"

# Check Python version
python_version=$(python3 --version 2>&1 | cut -d" " -f2)
required_version="3.7"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
  echo "ERROR: Python $required_version or higher is required. You have $python_version"
  exit 1
fi

echo "✅ Python version check passed: $python_version"

# Create virtual environment
if [ ! -d "venv" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv venv
  echo "✅ Virtual environment created"
else
  echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Dependencies installed successfully"

# Check for .env file
if [ ! -f ".env" ]; then
  echo "Creating .env file template..."
  cat > .env << EOF
# API Keys for AI services

# Required for scraping 
# OPEN_API_KEY=your_openai_api_key

# Required for blood test kits advisor
# ANTHROPIC_API_KEY=your_anthropic_api_key  
# OPENAI_API_KEY=your_openai_api_key
# GEMINI_API_KEY=your_gemini_api_key  # Will be used as the source for GOOGLE_API_KEY

EOF
  echo "⚠️ .env file created. Please edit it to add your API keys."
else
  echo "✅ .env file already exists - will use your existing API keys"
fi

echo "-------------------------------------------------------"
echo "Installation completed! You can now run the Blood Test Analyzer."
echo ""
echo "IMPORTANT: The virtual environment is currently activated for this session."
echo "If you close this terminal, you'll need to reactivate it with:"
echo "  source venv/bin/activate"
echo ""
echo "Usage examples (with virtual environment activated):"
echo "  python cli.py -q \"Which blood test package gives the most comprehensive coverage?\""
echo "  python cli.py -i    # Interactive mode"
echo ""
echo "Make sure your .env file contains valid API keys for all three services."
echo "-------------------------------------------------------"
