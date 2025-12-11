# CRC Learning Strategy - Učící se Offline Mode

## 🎯 Problém

**Otázka:** Jsou CRC hodnoty (00167, 54590, 34500) **univerzální** pro všechny BOXy?
- Možná ano (CRC je od obsahu odpovědi, ne od requestu)
- Možná ne (CRC může být BOX-specifické)

**Riziko:** Hardcoded CRC nemusí fungovat na jiných BOXech!

## ✅ Řešení: Učící se proxy

Místo hardcoded CRC → **proxy se naučí správné odpovědi z cloudu** během normálního provozu!

### Fáze 1: Learning Mode (běží vždy)

```python
class ResponseLearner:
    """Učí se správné cloud odpovědi během forward mode"""
    
    def __init__(self):
        self.learned_responses = {
            'ACK_STANDARD': None,      # ACK s GetActual
            'ACK_UNSTABLE': None,      # ACK bez GetActual  
            'END_NO_SETTINGS': None,   # END bez času
            'END_WITH_TIME': None,     # END s časem (template)
        }
        self.confidence = {}  # Kolikrát jsme viděli každou odpověď
        
    def observe(self, box_request: str, cloud_response: str):
        """Zaznamenej request→response pár"""
        
        # Detekuj typ odpovědi
        if '<Result>ACK</Result><ToDo>GetActual</ToDo>' in cloud_response:
            response_type = 'ACK_STANDARD'
        elif '<Result>ACK</Result><CRC>' in cloud_response and '<ToDo>' not in cloud_response:
            response_type = 'ACK_UNSTABLE'
        elif '<Result>END</Result><CRC>' in cloud_response and '<Time>' not in cloud_response:
            response_type = 'END_NO_SETTINGS'
        elif '<Result>END</Result><Time>' in cloud_response:
            response_type = 'END_WITH_TIME'
        else:
            return  # Neznámý typ
            
        # První vidění nebo verifikace
        if self.learned_responses[response_type] is None:
            self.learned_responses[response_type] = cloud_response
            self.confidence[response_type] = 1
            logger.info(f"✅ Learned {response_type}: {cloud_response[:80]}...")
        elif self.learned_responses[response_type] == cloud_response:
            self.confidence[response_type] += 1
            if self.confidence[response_type] in [10, 100, 1000]:
                logger.info(f"✅ Confidence {response_type}: {self.confidence[response_type]}x")
        else:
            # Jiná odpověď stejného typu!
            logger.warning(f"⚠️ Different {response_type}! Known: {self.learned_responses[response_type][:50]}, New: {cloud_response[:50]}")
    
    def is_ready(self) -> bool:
        """Máme naučené všechny základní odpovědi?"""
        return (
            self.learned_responses['ACK_STANDARD'] is not None and
            self.learned_responses['END_NO_SETTINGS'] is not None and
            self.confidence.get('ACK_STANDARD', 0) >= 5  # Viděli jsme to alespoň 5x
        )
    
    def get_fallback_responses(self) -> dict[str, str]:
        """Vrať naučené odpovědi pro offline mode"""
        if not self.is_ready():
            # Fallback na hardcoded (pro první spuštění)
            logger.warning("⚠️ Learning incomplete! Using hardcoded responses.")
            return {
                'ACK_STANDARD': '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
                'END_NO_SETTINGS': '<Frame><Result>END</Result><CRC>34500</CRC></Frame>',
            }
        
        return self.learned_responses
```

### Fáze 2: Persistence

Naučené odpovědi **ukládej na disk** → přežijí restart!

```python
LEARNED_RESPONSES_PATH = "/data/learned_responses.json"

class ResponseLearner:
    def save_to_disk(self):
        """Ulož naučené odpovědi"""
        data = {
            'responses': self.learned_responses,
            'confidence': self.confidence,
            'last_updated': datetime.datetime.now().isoformat(),
        }
        with open(LEARNED_RESPONSES_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"💾 Saved learned responses to {LEARNED_RESPONSES_PATH}")
    
    def load_from_disk(self):
        """Načti předchozí naučené odpovědi"""
        if not os.path.exists(LEARNED_RESPONSES_PATH):
            logger.info("📖 No learned responses found, will learn from scratch")
            return
            
        with open(LEARNED_RESPONSES_PATH, 'r') as f:
            data = json.load(f)
            
        self.learned_responses = data['responses']
        self.confidence = data['confidence']
        logger.info(f"✅ Loaded learned responses from {data['last_updated']}")
        logger.info(f"   ACK_STANDARD confidence: {self.confidence.get('ACK_STANDARD', 0)}x")
```

### Fáze 3: Integrace do Proxy

```python
class OIGProxy:
    def __init__(self):
        self.learner = ResponseLearner()
        self.learner.load_from_disk()  # Načti při startu
        self.save_counter = 0
        
    async def _forward(self, reader_from, writer_to, direction):
        """Bidirectional forward WITH learning"""
        while True:
            data = await reader_from.read(8192)
            if not data:
                break
                
            message = data.decode('utf-8', errors='ignore')
            
            # UČENÍ: Pozoruj cloud odpovědi
            if direction == 'proxy_to_box':  # Cloud → BOX
                # Najdi předchozí BOX request z historie
                last_request = getattr(self, '_last_box_request', None)
                if last_request:
                    self.learner.observe(last_request, message)
                    
                    # Ulož každých 100 framů
                    self.save_counter += 1
                    if self.save_counter % 100 == 0:
                        self.learner.save_to_disk()
                        
            elif direction == 'box_to_proxy':  # BOX → Cloud
                self._last_box_request = message
            
            writer_to.write(data)
            await writer_to.drain()
    
    async def _run_offline_mode(self, box_reader, box_writer):
        """Offline mode using LEARNED responses"""
        
        if not self.learner.is_ready():
            logger.warning("⚠️ Offline mode starting but learning incomplete!")
        
        responses = self.learner.get_fallback_responses()
        
        while True:
            try:
                data = await asyncio.wait_for(box_reader.read(8192), timeout=120)
                if not data:
                    break
                    
                frame = data.decode('utf-8', errors='ignore')
                
                # Detekuj typ requestu
                if '<Result>IsNewSet</Result>' in frame:
                    response = responses['END_NO_SETTINGS']
                else:
                    response = responses['ACK_STANDARD']
                
                # Pošli naučenou odpověď!
                box_writer.write(response.encode('utf-8'))
                await box_writer.drain()
                
                logger.info(f"📤 Sent learned response: {response[:60]}...")
                
            except asyncio.TimeoutError:
                logger.warning("⚠️ BOX timeout in offline mode")
                break
```

## 🎯 Výhody Learning Approach

### ✅ BOX-agnostic
- Funguje s **jakýmkoliv BOXem**
- CRC se naučí automaticky z cloudu
- Žádné hardcoded hodnoty

### ✅ Self-validating
- Pokud cloud pošle jiné CRC → warning log
- Confidence counter ukazuje spolehlivost
- Persistence přežije restart

### ✅ Bezpečné fallback
- Pokud learning není kompletní → hardcoded default
- První ~10 framů použijí hardcoded
- Pak přepne na learned

### ✅ Debugovatelné
```json
{
  "responses": {
    "ACK_STANDARD": "<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>",
    "END_NO_SETTINGS": "<Frame><Result>END</Result><CRC>34500</CRC></Frame>"
  },
  "confidence": {
    "ACK_STANDARD": 2847,
    "END_NO_SETTINGS": 64
  },
  "last_updated": "2025-12-10T14:23:15"
}
```

## 📊 Timeline implementace

**Immediate (2 hodiny):**
- ResponseLearner class
- observe() v _forward()
- save/load JSON

**Testing (1 hodina):**
- Spustit s cloudem → naučí se
- Simulovat cloud outage → použije learned
- Restart → persistence funguje

**Production:**
- První běh: 10-20 framů → naučí se ACK
- 1h provozu: 400+ framů → high confidence
- Restart: loaded responses → okamžitě ready

## 🎯 Doporučení

**ANO - použij learning approach!**

**Důvody:**
1. ✅ Univerzální (jakýkoliv BOX)
2. ✅ Self-validating (detekuje změny)
3. ✅ Bezpečné (hardcoded fallback)
4. ✅ Debugovatelné (JSON soubor)
5. ✅ Minimální overhead (jen observe při forward)

**Ne - nepoužívej pure hardcoded!**

**Rizika hardcoded:**
1. ❌ Může nefungovat na jiných BOXech
2. ❌ Žádná validace
3. ❌ Hard to debug (proč to nefunguje?)

---

## 🚀 Next Steps

Chceš implementovat learning mode?

**Co to znamená:**
- Proxy běží normálně (forward mode)
- **Tiše** pozoruje cloud odpovědi
- Ukládá je do `/data/learned_responses.json`
- Při offline mode → použije naučené odpovědi
- Při restartu → načte z disku

**Implementace:** ~2-3 hodiny
**Risk:** Nízké (nepřidává žádnou logiku do forward mode, jen observation)
