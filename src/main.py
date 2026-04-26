from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "API is running!!!"}

class PromptRequest(BaseModel):
    prompt: str


@app.post("/generate")
def generate(req: PromptRequest):
    return {"response": f"AI says: {req.prompt}"}