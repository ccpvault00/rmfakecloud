package models

import (
	"encoding/json"
	"fmt"
	"io"
	"path"
	"strings"

	"github.com/ddvk/rmfakecloud/internal/storage"
	"github.com/ddvk/rmfakecloud/internal/storage/exporter"
	"github.com/juruen/rmapi/archive"
	"github.com/juruen/rmapi/encoding/rm"
	log "github.com/sirupsen/logrus"
)

// ArchiveFromHashDoc reads an archive
func ArchiveFromHashDoc(doc *HashDoc, rs RemoteStorage) (*exporter.MyArchive, error) {
	uuid := doc.EntryName
	a := exporter.MyArchive{
		Zip: archive.Zip{
			UUID: uuid,
		},
	}

	pageMap := make(map[string]string)
	for _, f := range doc.Files {
		filext := path.Ext(f.EntryName)
		name := strings.TrimSuffix(path.Base(f.EntryName), filext)
		switch filext {
		case storage.ContentFileExt:
			blob, err := rs.GetReader(f.Hash)
			if err != nil {
				return nil, err
			}
			defer blob.Close()
			contentBytes, err := io.ReadAll(blob)
			if err != nil {
				return nil, err
			}
			a.Content, err = parseContent(contentBytes)
			if err != nil {
				return nil, err
			}
		case storage.EpubFileExt:
			// the tablet keeps a PDF rendition next to an EPUB; that is what gets annotated
			if a.PayloadReader != nil {
				continue
			}
			fallthrough
		case storage.PdfFileExt:
			if a.PayloadReader != nil {
				a.PayloadReader.Close()
			}
			blob, err := rs.GetReader(f.Hash)
			if err != nil {
				return nil, err
			}
			// defer blob.Close()
			// contentBytes, err := ioutil.ReadAll(blob)
			// if err != nil {
			// 	return nil, err
			// }
			// a.Payload = contentBytes
			//HACK:
			a.PayloadReader = blob.(io.ReadSeekCloser)

		case ".json":
			//metadata
		case storage.RmFileExt:
			log.Debug("adding page ", name)
			pageMap[name] = f.Hash
		}
	}

	for _, p := range a.Content.Pages {
		if hash, ok := pageMap[p]; ok {
			log.Debug("page ", hash)
			reader, err := rs.GetReader(hash)
			if err != nil {
				return nil, err
			}
			pageBin, err := io.ReadAll(reader)
			if err != nil {
				return nil, err
			}
			rmpage := rm.New()
			err = rmpage.UnmarshalBinary(pageBin)
			if err != nil {
				return nil, err
			}

			page := archive.Page{
				Data:     rmpage,
				Pagedata: "Blank",
			}
			a.Pages = append(a.Pages, page)
		}
	}

	return &a, nil
}

// pageTag is a page tag as written by firmware 3.x (formatVersion 2 .content).
type pageTag struct {
	Name string `json:"name"`
}

// parseContent decodes a .content file. rmapi's archive.Content declares pageTags as
// []string, but firmware 3.x writes objects ({"name","pageId","timestamp"}), which made
// every export of a document with tagged pages fail with a 500. The outer Tags field
// shadows the embedded one so both shapes are accepted.
func parseContent(data []byte) (archive.Content, error) {
	var c struct {
		archive.Content
		Tags json.RawMessage `json:"pageTags"`
	}
	if err := json.Unmarshal(data, &c); err != nil {
		return archive.Content{}, err
	}
	if len(c.Tags) == 0 || string(c.Tags) == "null" {
		return c.Content, nil
	}

	var names []string
	if err := json.Unmarshal(c.Tags, &names); err == nil {
		c.Content.Tags = names
		return c.Content, nil
	}
	var tags []pageTag
	if err := json.Unmarshal(c.Tags, &tags); err != nil {
		return archive.Content{}, fmt.Errorf("pageTags: neither strings nor tag objects: %w", err)
	}
	c.Content.Tags = make([]string, 0, len(tags))
	for _, t := range tags {
		c.Content.Tags = append(c.Content.Tags, t.Name)
	}
	return c.Content, nil
}
