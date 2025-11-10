import asyncio
import httpx
from fastapi import FastAPI, WebSocket, Depends
from pydantic import BaseModel
from redis.asyncio import Redis

app = FastAPI()
redis = Redis(host="localhost", port=6379, db=0)


class TokenInfo(BaseModel):
    token_address: str
    token_name: str
    token_ticker: str
    price_sol: float
    market_cap_sol: float
    volume_sol: float
    liquidity_sol: float
    transaction_count: int
    price_1hr_change: float
    price_24hr_change: float
    protocol: str


@app.get("/token/{token_address}", response_model=TokenInfo)
async def get_token_info(token_address: str):
    token_data = await redis.get(token_address)
    if token_data:
        return TokenInfo.parse_raw(token_data)
    else:
        endpoint = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(endpoint)

        if response.status_code == 200:
            data = response.json()
            if "pairs" in data and len(data["pairs"]) > 0:
                pair = data["pairs"][0]
                # use safe lookups with expected Dexscreener keys (h24, h1, etc.)
                txns = pair.get("txns", {}) or {}
                transaction_count = sum(
                    (t.get("buys", 0) + t.get("sells", 0)) for t in txns.values()
                )

                token_info = TokenInfo(
                    token_address=pair.get("baseToken", {}).get("address", ""),
                    token_name=pair.get("baseToken", {}).get("name", ""),
                    token_ticker=pair.get("baseToken", {}).get("symbol", ""),
                    price_sol=float(pair.get("priceNative") or 0),
                    market_cap_sol=float(pair.get("marketCap", 0) or 0),
                    # Dexscreener uses keys like 'h24' and 'h1' for 24h/1h
                    volume_sol=float(pair.get("volume", {}).get("h24", 0) or 0),
                    liquidity_sol=float(pair.get("liquidity", {}).get("base", 0) or 0),
                    transaction_count=int(transaction_count),
                    price_1hr_change=float(pair.get("priceChange", {}).get("h1", 0) or 0),
                    price_24hr_change=float(pair.get("priceChange", {}).get("h24", 0) or 0),
                    protocol=pair.get("dexId", "")
                )
                await redis.set(token_address, token_info.json(), ex=30)
                return token_info
            else:
                return {"error": "Token data not found"}
        else:
            return {"error": "Token not found"}

@app.get("/tokens/", response_model=list[TokenInfo])
async def get_all_tokens():
    endpoint = "https://api.dexscreener.com/latest/dex/search?q=solana"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(endpoint)
    tokens = []
    if response.status_code == 200:
        for token_data in response.json().get("pairs", []):
            txns = token_data.get("txns", {}) or {}
            transaction_count = sum((t.get("buys", 0) + t.get("sells", 0)) for t in txns.values())

            token_info = TokenInfo(
                token_address=token_data.get("baseToken", {}).get("address", ""),
                token_name=token_data.get("baseToken", {}).get("name", ""),
                token_ticker=token_data.get("baseToken", {}).get("symbol", ""),
                price_sol=float(token_data.get("priceNative") or 0),
                market_cap_sol=float(token_data.get("marketCap", 0) or 0),
                volume_sol=float(token_data.get("volume", {}).get("h24", 0) or 0),
                liquidity_sol=float(token_data.get("liquidity", {}).get("base", 0) or 0),
                transaction_count=int(transaction_count),
                    price_1hr_change=float(token_data.get("priceChange", {}).get("h1", 0) or 0),
                    price_24hr_change=float(token_data.get("priceChange", {}).get("h24", 0) or 0),
                    protocol=token_data.get("dexId", "")
            )
            tokens.append(token_info)
            await redis.set(token_info.token_address, token_info.json(), ex=30)
    return tokens

@app.websocket("/ws/tokens/")
async def websocket_endpoint(websocket: WebSocket, tokens: list[TokenInfo] = Depends(get_all_tokens)):
    await websocket.accept()
    try:
        while True:

            keys = await redis.keys('*')
            for key in keys:
                token_data = await redis.get(key)
                # only send if token data is changed since last send.
                if token_data:
                    token_info = TokenInfo.parse_raw(token_data)
                    for i, token in enumerate(tokens):
                        if token.token_address == token_info.token_address and token != token_info:
                            tokens[i] = token_info
                            await websocket.send_json(token_info.dict())
            await asyncio.sleep(10)  # send updates every 10 seconds
    except Exception as e:
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)