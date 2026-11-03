"""Gera uma chave Fernet para SECRETS_ENCRYPTION_KEY. Guarde no gerenciador de segredos, nunca no Git.
ATENÇÃO: perder/trocar a chave torna ilegíveis os tokens já cifrados (será preciso reconfigurá-los)."""
from cryptography.fernet import Fernet

print(Fernet.generate_key().decode())
