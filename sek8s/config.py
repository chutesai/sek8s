import os
from typing import Optional, Union, get_args, get_origin, get_type_hints

class Config:

    def __init__(self):
        self._load()

    def _load(self):
        """Load configuration from environment variables based on type hints."""
        properties: dict[str, type] = get_type_hints(self.__class__)
        
        for property_name, property_type in properties.items():
            env_var = property_name.upper()
            
            # Check if property has a default value
            has_default = hasattr(self.__class__, property_name) and hasattr(getattr(self.__class__, property_name), '__class__')
            if not has_default:
                # Check if it's defined as a class attribute with a default value
                has_default = property_name in self.__class__.__dict__
            
            # Check if type is Optional (Union[T, None])
            is_optional = (get_origin(property_type) is Union and 
                          type(None) in get_args(property_type))
            
            # Get the actual type if Optional
            if is_optional:
                actual_type = next(arg for arg in get_args(property_type) if arg is not type(None))
            else:
                actual_type = property_type
            
            value = os.environ.get(env_var)
            
            # Handle missing values
            if value is None:
                if is_optional:
                    setattr(self, property_name, None)
                    continue
                elif has_default:
                    # Keep the default value, don't set anything
                    continue
                else:
                    raise RuntimeError(f"Required environment variable '{env_var}' is not set")
            
            # Process based on actual type
            if actual_type == str:
                setattr(self, property_name, value)
                
            elif actual_type == int:
                try:
                    setattr(self, property_name, int(value))
                except ValueError:
                    raise RuntimeError(f"Invalid integer value for '{env_var}': '{value}'")
                
            elif actual_type == list[str]:
                # Split by comma and strip whitespace
                setattr(self, property_name, [item.strip() for item in value.split(",") if item.strip()])
                
            elif actual_type == bool:
                if value.lower() in ("true", "1", "yes", "on"):
                    setattr(self, property_name, True)
                elif value.lower() in ("false", "0", "no", "off"):
                    setattr(self, property_name, False)
                else:
                    raise RuntimeError(f"Invalid boolean value for '{env_var}': '{value}'")
                    
            else:
                raise RuntimeError(f"Unsupported type for configuration value: {actual_type}")

class AdmissionSettings(Config):

    tls_cert_file: Optional[str]
    tls_private_key_file: Optional[str]
    controller_port: int = 8884
    debug: bool = False


class OPAEngineSettings(Config):

    policy_dir: str
    allowed_registries: list[str]
    debug: bool = False
