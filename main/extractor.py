'''
same thing as classify here, i have no idea what it needs im just assuming

Capability: Extract data

For comparison requests, read the SI and BL attachments and identify
the corresponding shipment fields.
'''

from classify import Classify


class Extractor:
    def __init__(self, SI, BL, classifier: Classify) -> None:
        self.SI = SI
        self.BL = BL
        self.classifier = classifier
        
        self.name = None
        self.ports = None
        self.quantities = None
        self.weights = None

    def extract_data(self):
        pass