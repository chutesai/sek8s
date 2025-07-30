# These DON'T trigger admission controllers:
kubectl logs pod-name
kubectl exec -it pod-name -- /bin/bash
kubectl port-forward pod-name 8080:80
kubectl attach pod-name
kubectl proxy
kubectl cp file pod-name:/tmp/

** Need to add policies to restrict these actions **

Ensure etcdctl is not installed on the host and can not be installed

Ensure CSR can not be created