import sys
from pathlib import Path
from common.config_loader import load_config, AppConfig

def test_config():
    app_name = "test-app"
    yaml_path = Path("test_config.yaml")
    
    print(f"Testing config load for {app_name}...")
    try:
        config = load_config(app_name, yaml_path)
        print("Successfully loaded config!")
        print(f"App Name: {config.app_name}")
        print(f"Discord Mode: {config.discord.mode}")
        print(f"Schedule Cron: {config.schedule.cron}")
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_config()
