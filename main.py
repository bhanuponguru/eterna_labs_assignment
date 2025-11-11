import asyncio
import os
import os
import httpx
from fastapi import FastAPI, WebSocket, Depends, WebSocketDisconnect, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from redis.asyncio import Redis
from dotenv import load_dotenv

load_dotenv()

ttl=os.environ.get("TTL", 30)

app = FastAPI()
redis = Redis(host="localhost", port=6379, db=0)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # note: browsers disallow credentials with "*"
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        return TokenInfo.model_validate_json(token_data)
    else:
        endpoint = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
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
                await redis.set(token_address, token_info.model_dump_json(), ex=ttl)
                return token_info
            else:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token data not found")
        else:
            raise HTTPException(status_code=response.status_code, detail=f"Token not found error {response.status_code}")

@app.get("/tokens/", response_model=list[TokenInfo])
async def get_all_tokens():
    endpoint = "https://api.dexscreener.com/latest/dex/search?q=solana"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(endpoint)
    tokens = []
    if response.status_code != 200:
        # upstream error
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch tokens from upstream")

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
            await redis.set(token_info.token_address, token_info.model_dump_json(), ex=ttl)
    return tokens

@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        # initially, load all tokens (list of TokenInfo instances)
        tokens = await get_all_tokens()

        while True:
            for i in range(len(tokens)):
                old = tokens[i]
                try:
                    latest = await get_token_info(old.token_address)
                except HTTPException as e:
                    # upstream returned a proper HTTP error; log and skip
                    print(f"get_token_info HTTP error for {old.token_address}: {e.detail}")
                    continue
                except Exception as e:
                    # network or parsing error; skip this token this round
                    print(f"Error fetching token {old.token_address}: {e}")
                    continue

                # At this point we expect `latest` to be a TokenInfo model
                if latest != old:
                    # use model_dump() to get a plain dict (pydantic v2)
                    payload = latest.model_dump()
                    await websocket.send_json(payload)
                    # update the stored copy so future diffs are correct
                    tokens[i] = latest

            await asyncio.sleep(10)
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    finally:
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)