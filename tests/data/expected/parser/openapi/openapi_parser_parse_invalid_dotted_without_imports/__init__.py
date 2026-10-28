class Shipment(BaseModel):
    trackingNumber: Optional[str] = None


class SaveRequest(BaseModel):
    shipment: Optional[Shipment] = None
    trifecta: Optional[SaveTrifectaV2.Field1] = None
