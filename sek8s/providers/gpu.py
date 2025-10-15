

from sek8s.models import GPU, DeviceInfo
import pynvml


class GpuDeviceProvider:

    def get_device_info(self) -> list[GPU]:
        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
        except pynvml.NVMLError as e:
            raise RuntimeError(f"NVML init failed: {e}")
        
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
                    processors="", # not available?
                    sxm=None, # Not needed since we are not using GraVal
                    clock_rate=pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS),
                    max_threads_per_processor="", # not available?
                    concurrent_kernels="", # not available?
                    ecc=bool(pynvml.nvmlDeviceGetEccMode(handle)[0])
                ),
                model_short_ref=name.lower().split()[-1]  # e.g., 'a6000'
            )

            gpus.append(gpu)
        
        pynvml.nvmlShutdown()
        return gpus