from pydantic import BaseModel, Field
from typing import Optional, Dict
from uuid import UUID

class MemoryInfo(BaseModel):
    total: int = Field(..., description="Total memory in bytes")
    used: int = Field(..., description="Used memory in bytes")
    free: int = Field(..., description="Free memory in bytes")

class UtilizationInfo(BaseModel):
    gpu: int = Field(..., description="GPU utilization percentage")
    memory: int = Field(..., description="Memory utilization percentage")

class DeviceInfo(BaseModel):
    uuid: str = Field(..., description="Unique GPU identifier")
    name: str = Field(..., description="Full GPU product name, e.g., 'NVIDIA RTX A6000'")
    memory: int = Field(..., description="")
    major: Optional[int] = Field(..., description="")
    minor: Optional[int] = Field(..., description="")
    clock_rate: float = Field(..., description="")
    ecc: Optional[bool] = Field(..., description="")
    model_short_ref: str = Field(..., description="Short name for the GPU model")

    class Config:
        json_encoders = {
            UUID: str  # Ensure UUIDs serialize as strings
        }

class GPU(BaseModel):
    # gpu_id: str = Field(..., description="Unique GPU identifier (matches device_info.uuid)")
    device_info: DeviceInfo = Field(..., description="Detailed GPU information")
    model_short_ref: str = Field(..., description="Short reference for GPU model, e.g., 'a6000'")

    class Config:
        json_encoders = {
            UUID: str  # Ensure UUIDs serialize as strings
        }