def chunk(text, max_words=180):
    """Fixed windows. Enough for the synthetic documents (one chunk each);
    the NIST text will need the heading-aware chunker from DocGuide."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]