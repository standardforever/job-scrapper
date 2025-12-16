

# =============================================================================
# Text Processing Utilities
# =============================================================================


class TextProcessor:
    @staticmethod
    def append_non_overlapping(existing: str, new: str, overlap_check_size: int = 100) -> str:
        if not existing:
            return new
        if not new:
            return existing

        overlap_segment = existing[-overlap_check_size:]
        overlap_pos = new.find(overlap_segment)

        if overlap_pos != -1:
            return existing + new[overlap_pos + len(overlap_segment):]
        return existing + "\n\n" + new

    @staticmethod
    def split_into_chunks(text: str, chunk_size: int = 150000) -> list[str]:
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end < len(text):
                last_newline = text.rfind("\n", start, end)
                if last_newline > start:
                    end = last_newline + 1
            chunks.append(text[start:end])
            start = end

        return chunks

    @staticmethod
    def normalize_url(url: str, domain: str) -> str:
        if not url:
            return ""
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return f"https://{domain}{url}"
        return f"https://{domain}/{url}"
