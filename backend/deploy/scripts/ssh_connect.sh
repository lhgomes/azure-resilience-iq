cd ../vm-terraform/
RG=$(terraform output -raw resource_group_name)
VM=$(terraform output -raw vm_name)
az ssh config --resource-group "$RG" --name "$VM" --file /tmp/azure-vm-ssh.conf --overwrite
HOST_ALIAS=$(awk '/^Host / { print $2; exit }' /tmp/azure-vm-ssh.conf)
ssh -F /tmp/azure-vm-ssh.conf -o StrictHostKeyChecking=accept-new "$HOST_ALIAS"