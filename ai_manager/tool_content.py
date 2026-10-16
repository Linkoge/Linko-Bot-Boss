"""Image-bearing application results; provider transports keep images out of JSON text."""

import base64
import json
from dataclasses import dataclass


@dataclass
class ImageResult:
    metadata: dict
    images: list[bytes]

    def content_items(self):
        return [{'type': 'inputText', 'text': json.dumps(self.metadata)}, *[
            {'type': 'inputImage', 'imageUrl': 'data:image/png;base64,' + base64.b64encode(data).decode()}
            for data in self.images]]
