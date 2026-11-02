package emulation

import (
	"encoding/json"
	"math/big"
	"os"
	"testing"
)

type vector struct {
	PassportHash string `json:"passport_hash"`
	ClientID     string `json:"client_id"`
	Score        int    `json:"score"`
	Delinquency  bool   `json:"delinquency"`
	Requests90d  int    `json:"requests_90d"`
	Bucket       string `json:"bucket"`
	Index        string `json:"index"`
	Smev         string `json:"smev"`
}

// Паритет с Python: векторы лежат в testdata и получены из credit_core.emulation.
func TestParityWithPython(t *testing.T) {
	raw, err := os.ReadFile("../../testdata/emulation_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []vector
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	if len(vectors) < 1000 {
		t.Fatalf("векторов %d, ждали 1000", len(vectors))
	}
	seen := map[string]int{}
	for _, v := range vectors {
		got := Score(v.PassportHash)
		if got.Score != v.Score || got.Delinquency != v.Delinquency || got.Requests90d != v.Requests90d {
			t.Fatalf("Score(%s) = %+v, Python: %d/%v/%d", v.PassportHash, got, v.Score, v.Delinquency, v.Requests90d)
		}
		bucket, index := Bucket(v.PassportHash)
		want, _ := new(big.Int).SetString(v.Index, 10)
		if bucket != v.Bucket || index.Cmp(want) != 0 {
			t.Fatalf("Bucket(%s) = %s/%s, Python: %s/%s", v.PassportHash, bucket, index, v.Bucket, v.Index)
		}
		if got := SmevStatus(v.ClientID); got != v.Smev {
			t.Fatalf("SmevStatus(%s) = %s, Python: %s", v.ClientID, got, v.Smev)
		}
		seen[bucket]++
		seen[v.Smev]++
	}
	// в 1000 векторах должны встретиться все ветки формул
	for _, k := range []string{Good, Medium, Bad, "MATCH", "MISMATCH"} {
		if seen[k] == 0 {
			t.Errorf("в векторах нет случая %s", k)
		}
	}
}

func TestFormulaBranches(t *testing.T) {
	// ручные проверки границ формулы: h=0..9 найдём перебором хэшей
	byH := map[int]BkiScore{}
	for i := 0; i < 2000 && len(byH) < 10; i++ {
		s := Score(string(rune('a'+i%26)) + string(rune('A'+i/26%26)) + string(rune('0'+i/676)))
		byH[s.Score] = s
	}
	for _, score := range []int{600, 620, 640, 660, 680, 700, 450, 500, 550, 400} {
		if _, ok := byH[score]; !ok {
			t.Errorf("не встретился скор %d", score)
		}
	}
	if !byH[400].Delinquency || byH[700].Delinquency {
		t.Error("просрочка только у h=9")
	}
}
