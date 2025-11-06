from typing import Optional
from loguru import logger
from sek8s.exceptions import NvmlException
from sek8s.models import GPU, DeviceInfo
import pynvml

def sanitize_gpu_id(gpu_id):
    """
    Remove 'GPU-' prefix and all hyphens from a GPU ID.
    
    Args:
        gpu_id: String containing the GPU ID
        
    Returns:
        Sanitized GPU ID string
    """
    # Remove 'GPU-' prefix (case-insensitive)
    sanitized = gpu_id.replace('GPU-', '').replace('gpu-', '')
    # Remove all hyphens
    sanitized = sanitized.replace('-', '')
    return sanitized

class GpuDeviceProvider:

    def get_device_info(self, gpu_ids: Optional[list[str]]) -> list[DeviceInfo]:
        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
        
            all_gpus = []
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                
                name = pynvml.nvmlDeviceGetName(handle)
                compute_capability = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                
                device_info=DeviceInfo(
                    uuid=sanitize_gpu_id(pynvml.nvmlDeviceGetUUID(handle)),
                    name=name,
                    memory=pynvml.nvmlDeviceGetMemoryInfo(handle).total,
                    major=compute_capability[0],
                    minor=compute_capability[1],
                    # pynvml returns in GHz but API expects it in MHz
                    clock_rate=pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS) * 1000,
                    ecc=bool(pynvml.nvmlDeviceGetEccMode(handle)[0]),
                    model_short_ref=name.lower().split()[-1]  # e.g., 'a6000'
                )

                all_gpus.append(device_info)
            
            pynvml.nvmlShutdown()
            gpus = self._filter_device_info(all_gpus, gpu_ids)
        except pynvml.NVMLError as e:
            logger.error(f"Exception retrieving device info from pynvml: {e}")
            raise NvmlException(f"Exception retrieving device info from pynvml: {e}")
        return gpus

    def _filter_device_info(self, all_devices: list[DeviceInfo], target_gpu_ids: Optional[list[str]]):
        filtered_devices = all_devices
        if target_gpu_ids:
            formatted_uuids = [sanitize_gpu_id(gpu_id) for gpu_id in target_gpu_ids]
            if formatted_uuids:
                filtered_devices = [gpu for gpu in all_devices if gpu.uuid in formatted_uuids]

        return filtered_devices