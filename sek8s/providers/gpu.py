

from loguru import logger
from sek8s.exceptions import NvmlException
from sek8s.models import GPU, DeviceInfo
import pynvml


class GpuDeviceProvider:

    def get_device_info(self) -> list[GPU]:
        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
        
            gpus = []
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                
                name = pynvml.nvmlDeviceGetName(handle)
                compute_capability = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                
                gpu = GPU(
                    device_info=DeviceInfo(
                        uuid=pynvml.nvmlDeviceGetUUID(handle),
                        name=name,
                        memory=pynvml.nvmlDeviceGetMemoryInfo(handle).total,
                        major=compute_capability[0],
                        minor=compute_capability[1],
                        # pynvml returns in GHz but API expects it in MHz
                        clock_rate=pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS) * 1000,
                        ecc=bool(pynvml.nvmlDeviceGetEccMode(handle)[0])
                    ),
                    model_short_ref=name.lower().split()[-1]  # e.g., 'a6000'
                )

                gpus.append(gpu)
            
            pynvml.nvmlShutdown()
        except pynvml.NVMLError as e:
            logger.error(f"Exception retrieving device info from pynvml: {e}")
            raise NvmlException(f"Exception retrieving device info from pynvml: {e}")
        return gpus