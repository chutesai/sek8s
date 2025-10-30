import asyncio
import json
import os

from loguru import logger

from sek8s.exceptions import NvTrustException
import pynvml

from sek8s.providers.gpu import sanitize_gpu_id

class NvEvidenceProvider:
    """Async web server for admission webhook."""

    def __enter__(self):
        pynvml.nvmlInit()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            pynvml.nvmlShutdown()
        except:
            pass

        return False

    async def get_evidence(self, name: str, nonce: str, gpu_ids: list[str] = None) -> str:
        try:
            result = await asyncio.create_subprocess_exec(
                *["chutes-nvevidence", "--name", name, "--nonce", nonce],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/var/log/attestation-service"
            )

            await result.wait()

            if result.returncode == 0:
                result_output = await result.stdout.read()
                output_str = result_output.decode()
                
                # Get the last non-empty line
                lines = [line for line in output_str.strip().split('\n') if line.strip()]
                if not lines:
                    raise NvTrustException("No output from evidence command")
                
                evidence_json = lines[-1]
                logger.info(f"Successfully generated NVTrust evidence")
                
                filtered_evidence = self._filter_evidence(evidence_json, gpu_ids)
                return filtered_evidence
            else:
                result_output = await result.stderr.read()
                logger.error(f"Failed to gather GPU evidence:{result_output}")
                raise NvTrustException(f"Failed to gather evidence.")
        except Exception as e:
            logger.error(f"Unexpected error gathering GPU evidence:{e}")
            raise NvTrustException(f"Unexpected error gathering GPU evidence.")

    def _filter_evidence(self, evidence: str, target_gpu_ids: list[str]):
        filtered_evidence = evidence
        if target_gpu_ids:
            evidence_list = json.loads(evidence)
            all_gpu_uids = self._get_gpu_ids()
            if len(target_gpu_ids) < len(all_gpu_uids):
                target_indices = [idx for idx, gpu_id in enumerate(all_gpu_uids) if gpu_id in target_gpu_ids]
                target_evidence = [evidence_list[idx] for idx in target_indices]
                filtered_evidence = json.dumps(target_evidence)
        
        return filtered_evidence

    def _get_gpu_ids(self):
        device_count = pynvml.nvmlDeviceGetCount()
        
        gpu_uids = []
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            gpu_uids.append(sanitize_gpu_id(pynvml.nvmlDeviceGetUUID(handle)))

        return gpu_uids
    