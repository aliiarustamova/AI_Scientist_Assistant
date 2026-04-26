"""
protocols.io API client for read-only protocol grounding.
"""
import os
import requests

PROTOCOLS_IO_TOKEN = os.environ.get("PROTOCOLS_IO_TOKEN")
BASE_URL = "https://www.protocols.io/api"


class ProtocolsIoError(Exception):
    """Custom exception for protocols.io API errors."""
    pass


def get_headers():
    """Return headers for API requests."""
    headers = {
        "Content-Type": "application/json"
    }
    if PROTOCOLS_IO_TOKEN:
        headers["Authorization"] = f"Bearer {PROTOCOLS_IO_TOKEN}"
    return headers


def search_protocols(query: str, limit: int = 5) -> list:
    """
    Search public protocols on protocols.io.
    
    Args:
        query: Search query string
        limit: Maximum number of results (default 5)
    
    Returns:
        List of normalized protocol candidates
    """
    if not PROTOCOLS_IO_TOKEN:
        return []
    
    try:
        response = requests.get(
            f"{BASE_URL}/v3/protocols",
            params={
                "filter": "public",
                "key": query,
                "order_field": "relevance",
                "order_dir": "desc",
                "page_size": limit
            },
            headers=get_headers(),
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        candidates = []
        for item in data.get("items", []):
            protocol = {
                "id": str(item.get("id", "")),
                "title": item.get("title", ""),
                "description": item.get("description", "")[:500] if item.get("description") else "",
                "url": item.get("uri", ""),
                "doi": item.get("doi", ""),
                "uri": item.get("uri", ""),
                "source": "protocols.io",
                "materials_available": item.get("has_materials", False),
                "steps_available": item.get("has_steps", False),
                "relevance_reason": "",
                "relevance_score": None
            }
            candidates.append(protocol)
        
        return candidates
    
    except requests.RequestException:
        return []


def get_protocol_steps(protocol_id: str) -> list:
    """
    Fetch steps for a specific protocol.
    
    Args:
        protocol_id: The protocol ID
    
    Returns:
        List of protocol steps
    """
    if not PROTOCOLS_IO_TOKEN:
        return []
    
    try:
        response = requests.get(
            f"{BASE_URL}/v4/protocols/{protocol_id}/steps",
            headers=get_headers(),
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        steps = []
        for item in data.get("steps", []):
            step = {
                "step_number": item.get("ordinal", 0),
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "image_url": item.get("image", {}).get("url") if isinstance(item.get("image"), dict) else None
            }
            steps.append(step)
        
        return steps
    
    except requests.RequestException:
        return []


def get_protocol_materials(protocol_id: str) -> list:
    """
    Fetch materials for a specific protocol.
    
    Args:
        protocol_id: The protocol ID
    
    Returns:
        List of protocol materials
    """
    if not PROTOCOLS_IO_TOKEN:
        return []
    
    try:
        response = requests.get(
            f"{BASE_URL}/v3/protocols/{protocol_id}/materials",
            headers=get_headers(),
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        materials = []
        for item in data.get("materials", []):
            material = {
                "name": item.get("name", ""),
                "quantity": item.get("quantity", ""),
                "unit": item.get("unit", ""),
                "vendor": item.get("vendor", ""),
                "sku": item.get("catalog_number", ""),
                "url": item.get("url", "")
            }
            materials.append(material)
        
        return materials
    
    except requests.RequestException:
        return []


def get_protocol_bundle(query: str, selected_protocol_id: str = None) -> dict:
    """
    Get protocol context bundle for grounding experiment plans.
    
    Args:
        query: Search query (usually the hypothesis)
        selected_protocol_id: Optional specific protocol ID to use
    
    Returns:
        Protocol bundle with candidates, steps, materials, and gaps
    """
    # Check for missing token
    if not PROTOCOLS_IO_TOKEN:
        return {
            "grounding_status": "missing_token",
            "selection_mode": "none",
            "selected_protocol": None,
            "candidates": [],
            "steps": [],
            "materials": [],
            "gaps": ["protocols.io token not configured"]
        }
    
    # Search for protocols
    candidates = search_protocols(query, limit=5)
    
    if not candidates:
        return {
            "grounding_status": "no_matches",
            "selection_mode": "none",
            "selected_protocol": None,
            "candidates": [],
            "steps": [],
            "materials": [],
            "gaps": ["No matching protocols found for query"]
        }
    
    # Determine which protocol to use
    selected = None
    selection_mode = "none"
    
    if selected_protocol_id:
        # User selected a specific protocol
        for candidate in candidates:
            if candidate["id"] == selected_protocol_id:
                selected = candidate
                selection_mode = "user"
                break
        if not selected:
            # Fallback to first match if ID not found
            selected = candidates[0]
            selection_mode = "auto"
    else:
        # Auto-select first match
        selected = candidates[0]
        selection_mode = "auto"
    
    # Fetch steps and materials
    steps = []
    materials = []
    gaps = []
    
    try:
        if selected:
            steps = get_protocol_steps(selected["id"])
            materials = get_protocol_materials(selected["id"])
            
            if not steps:
                gaps.append("Protocol steps not available")
            if not materials:
                gaps.append("Protocol materials not available")
    
    except Exception as e:
        gaps.append(f"Failed to fetch protocol details: {str(e)}")
    
    return {
        "grounding_status": "success",
        "selection_mode": selection_mode,
        "selected_protocol": selected,
        "candidates": candidates,
        "steps": steps,
        "materials": materials,
        "gaps": gaps
    }