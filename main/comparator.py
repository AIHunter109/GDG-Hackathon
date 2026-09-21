"""Compare the seven official SI/BL fields."""

FIELDS = (
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
)


def compare_documents(si_fields, bl_fields):
    mismatches = []
    missing = []
    for field in FIELDS:
        si, bl = si_fields.get(field), bl_fields.get(field)
        if (
            not si
            or not bl
            or si["normalized_value"] is None
            or bl["normalized_value"] is None
        ):
            missing.append(field)
            continue
        if si["normalized_value"] != bl["normalized_value"]:
            mismatches.append(
                {
                    "field": field,
                    "si": si,
                    "bl": bl,
                    "reason": f"Normalized values differ: {si['normalized_value']} vs {bl['normalized_value']}",
                }
            )
    return {"mismatches": mismatches, "missing_fields": missing}


class Comparator:
    def __init__(self, dataExtractor):
        self.extractor = dataExtractor

    def compare(self):
        return compare_documents(self.extractor.SI, self.extractor.BL)
