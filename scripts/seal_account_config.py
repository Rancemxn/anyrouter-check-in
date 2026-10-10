"""Encrypt the existing configuration for local, lossless session replacement."""

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b'Rancemxn/anyrouter-check-in:production:agentrouter-recovery-v1'


def seal_config(config: str, public_pem: bytes) -> str:
	assert isinstance(json.loads(config), list)
	public_key = serialization.load_pem_public_key(public_pem)
	assert isinstance(public_key, rsa.RSAPublicKey)
	key = AESGCM.generate_key(bit_length=256)
	nonce = os.urandom(12)
	ciphertext = AESGCM(key).encrypt(nonce, config.encode('utf-8'), AAD)
	encrypted_key = public_key.encrypt(
		key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
	)
	fields = {'key': encrypted_key, 'nonce': nonce, 'ciphertext': ciphertext}
	envelope = {k: base64.b64encode(v).decode('ascii') for k, v in fields.items()}
	return base64.b64encode(json.dumps(envelope).encode('ascii')).decode('ascii')


if __name__ == '__main__':
	blob = seal_config(os.environ['ANYROUTER_ACCOUNTS'], Path('recovery-public.pem').read_bytes())
	parts = [blob[i : i + 3000] for i in range(0, len(blob), 3000)]
	print(f'SEALED_CONFIG_PARTS={len(parts)}', flush=True)
	for i, part in enumerate(parts):
		print(f'SEALED_CONFIG_PART_{i:04d}={part}', flush=True)
