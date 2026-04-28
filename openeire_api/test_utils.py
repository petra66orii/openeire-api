from email.header import decode_header, make_header


def decode_sender_header(value):
    return str(make_header(decode_header(value)))
