"""Test vision_analyze_tool with different model."""
import os
import asyncio
import sys

# Load .env manually
env_path = os.path.expanduser('~/.hermes/.env')
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

# Clear the unhealthy cache for gemini
from agent.auxiliary_client import _unhealthy_providers
_unhealthy_providers.clear()

sys.path.insert(0, os.path.dirname(__file__))

async def test():
    from tools.vision_tools import vision_analyze_tool
    print(f'OPENROUTER_API_KEY set: {bool(os.environ.get("OPENROUTER_API_KEY"))}')
    result = await vision_analyze_tool(
        image_url='/home/ubuntu/bujo/uploads/test_vision.png',
        user_prompt='Describe los colores de esta imagen de prueba'
    )
    print('Result:')
    print(result[:1000])

if __name__ == '__main__':
    asyncio.run(test())
