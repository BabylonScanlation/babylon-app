import zlib


def encode(input_data, encoding_key_string):
    """
    Encode data using the Cloudflare challenge encoding pipeline:
    1. JSON stringify (if dict/list)
    2. UTF-8 encode
    3. DEFLATE compress (if >= 128 bytes)
    4. Prepend 3-byte header [0xFD, 0x01, compression_flag]
    5. XOR encrypt with FNV-1a seeded xorshift32 PRNG
    6. Custom base64 encode with 65-char key
    """
    import json

    if input_data is None:
        return ""

    if isinstance(input_data, (dict, list, tuple)):
        input_str = json.dumps(input_data, separators=(",", ":"))
    else:
        input_str = str(input_data)

    # Step 1: UTF-8 encode
    utf8_bytes = _utf8_encode(input_str)

    # Step 2: Try DEFLATE compression if large enough
    compression_flag = 0
    data_to_use = utf8_bytes

    if len(utf8_bytes) >= 128:
        compressed = _deflate_compress(utf8_bytes)
        if len(compressed) < len(utf8_bytes):
            data_to_use = compressed
            compression_flag = 1

    # Step 3: Prepend header
    header = bytes([0xFD, 0x01, compression_flag])
    payload = header + bytes(data_to_use)

    # Step 4: XOR encrypt
    encrypted = _xor_encrypt(payload, encoding_key_string)

    # Step 5: Custom base64 encode
    return _custom_b64_encode(encrypted, encoding_key_string)


def _utf8_encode(s):
    """Encode string to UTF-8 bytes."""
    return bytearray(s.encode('utf-8'))


def _deflate_compress(data):
    """Compress data using raw DEFLATE (no zlib header)."""
    compressor = zlib.compressobj(level=1, wbits=-15, memLevel=9)
    compressed = compressor.compress(bytes(data))
    compressed += compressor.flush()
    return compressed


def _fnv1a_hash(key_string):
    """FNV-1a hash of the key string."""
    h = 2166136261
    for c in key_string:
        h ^= ord(c)
        h = (h * 16777619) & 0xFFFFFFFF
    return h if h != 0 else 2779062077


def _xorshift32(x):
    """xorshift32 PRNG step."""
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= (x >> 17)
    x ^= (x << 5) & 0xFFFFFFFF
    return x & 0xFFFFFFFF


def _xor_encrypt(data, key_string):
    """XOR encrypt data using FNV-1a seeded xorshift32 PRNG."""
    seed = _fnv1a_hash(key_string)
    result = bytearray(data)
    state = seed
    for i in range(len(result)):
        state = _xorshift32(state)
        prng_byte = (state >> 24) & 0xFF
        key_byte = ord(key_string[i % 64])
        result[i] ^= prng_byte ^ key_byte
    return bytes(result)


def _custom_b64_encode(data, key_string):
    """Custom base64 encode using the 65-char key as alphabet (6 bits per char)."""
    result = []
    buffer = 0
    bits = 0
    for byte in data:
        buffer = (buffer << 8) | byte
        bits += 8
        while bits >= 6:
            bits -= 6
            result.append(key_string[(buffer >> bits) & 0x3F])
            buffer &= (1 << bits) - 1
    if bits > 0:
        result.append(key_string[(buffer << (6 - bits)) & 0x3F])
    return ''.join(result)
