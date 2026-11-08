f = open("sitemap.xml")
wf = open("isw_urls.txt", "a")
for l in f:
    if "https://understandingwar.org/research/russia-ukraine/" in l:
        wf.write(l[l.find("https://understandingwar.org/research/russia-ukraine/"):-7]+"\n")

f.close()
wf.close()