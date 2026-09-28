# dice_auto_apply/utils/config_manager.py

import os
import json
from pathlib import Path

_SINGLETON: "ConfigManager | None" = None

def get_config() -> "ConfigManager":
    """Return the process-wide ConfigManager singleton (avoids repeated disk reads)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = ConfigManager()
    return _SINGLETON

def invalidate_config_cache():
    """Force-reload on the next get_config() call (call after saving settings)."""
    global _SINGLETON
    _SINGLETON = None

class ConfigManager:
    """Manages application configuration settings."""
    
    def __init__(self):
        """Initialize the configuration manager."""
        self.config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        self.config_file = os.path.join(self.config_dir, "settings.json")
        self.config = self._load_config()
        
    def _load_config(self):
        """Load configuration from file."""
        # Ensure config directory exists
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
        
        # Create default config if it doesn't exist
        if not os.path.exists(self.config_file):
            default_config = {
                "search_queries": ["Java Developer", "Senior Java Developer", "Full Stack Java Developer",
                                    "Java Spring Boot Developer", "Java Microservices Developer"],
                "exclude_keywords": ["Manager", "Director", ".NET", "SAP", "Data Engineer", "Data Scientist",
                                      "Machine Learning Engineer", "w2 only", "only w2", "no c2c",
                                      "only on w2", "w2 profiles only", "tester", "f2f"],
                "include_keywords": ["Java", "Spring Boot", "Microservices", "Full Stack", "REST API",
                                      "Hibernate", "JPA", "AWS", "GCP", "Azure", "Kubernetes", "Docker",
                                      "Kafka", "Angular", "React", "TypeScript"],
        		"resume_profiles": [],
                "headless_mode": False,
                "job_application_limit": 50,
                "save_logs": True,
                "profile_name_boost_mode": "off"
            }
            
            # Write default config to file
            with open(self.config_file, 'w') as f:
                json.dump(default_config, f, indent=4)
            
            return default_config
        
        # Load existing config
        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
            # Backfill any keys added after initial install
            if 'profile_name_boost_mode' not in data:
                # Migrate old bool key if present
                old_val = data.pop('profile_name_boost', None)
                data['profile_name_boost_mode'] = 'off'
            return data
        except Exception as e:
            print(f"Error loading config: {e}")
            return {}
    
    def save_config(self):
        """Save configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def get(self, key, default=None):
        """Get a configuration value."""
        return self.config.get(key, default)
    
    def set(self, key, value):
        """Set a configuration value."""
        self.config[key] = value
        self.save_config()
