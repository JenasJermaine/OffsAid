"""OffsAid synthetic data generator (v5).

Builds multi-label training/validation data for the scam-language classifier.
Messages are assembled from hand-written clauses (English, Swahili, Sheng, code-mixed)
with random slot values, noise (typos, SMS spelling, emoji) and hard negatives.

IMPORTANT: validation uses clauses the training set never sees (clause-level hold-out),
but it is still synthetic. Real performance is only measured on the gold set of real messages.

Run:  python generate_synthetic.py   ->  synthetic_train.csv, synthetic_val.csv
"""
import random, re, hashlib
import pandas as pd

SEED = 42
N_TRAIN, N_VAL = 5000, 1000
FEE_WEIGHT = 0.5
LABELS = ["advance_fee_request", "personal_account_payment", "urgency_pressure",
          "secrecy", "guaranteed_outcome", "visa_travel_arranged", "off_platform_move"]
A, P, U, S, G, V, O = LABELS

# ---------------------------------------------------------------- slots
AMOUNTS = ["KES 2,000", "KES 5,000", "KES 7,500", "KES 10,000", "KES 15,000", "KES 20,000",
           "KES 25,000", "KES 30,000", "Ksh 12,000", "Ksh 3,500", "elfu tano", "elfu kumi",
           "elfu ishirini", "10k", "5k", "15k", "20k", "$150", "$300", "USD 200", "EUR 250"]
CLUBS = ["Gor Mahia", "AFC Leopards", "Tusker FC", "Bandari FC", "Kakamega Homeboyz",
         "Sofapaka", "Ulinzi Stars", "Nairobi United", "our academy", "the club"]
CITIES = ["Lisbon", "Madrid", "Dubai", "Doha", "Istanbul", "Paris", "Vienna", "Belgrade", "Riyadh", "Lyon"]
COUNTRIES = ["Spain", "Portugal", "Qatar", "UAE", "Turkey", "France", "Austria", "Serbia", "Saudi Arabia", "Belgium"]
WHENS = ["12 March", "Saturday", "next Monday", "15 April", "Jumamosi", "tomorrow", "Friday", "20 May"]
NAMES = ["John Kamau", "Peter Otieno", "Mary Wanjiru", "David Mwangi", "Samuel Kiptoo", "James Ochieng", "Grace Achieng"]
APPS = ["Telegram", "Signal", "WhatsApp group", "Instagram DM"]


def phone():
    return random.choice(["07", "01"]) + "".join(random.choice("0123456789") for _ in range(2)) + \
        " " + "".join(random.choice("0123456789") for _ in range(3)) + \
        " " + "".join(random.choice("0123456789") for _ in range(3))


def acct():
    return "".join(random.choice("0123456789") for _ in range(random.choice([10, 12])))


def handle():
    return "@" + random.choice(["scout", "fcagent", "talent", "proagent", "sportsmgr", "elite"]) + \
        random.choice(["_ke", "254", "_official", "01", ""]) + str(random.randint(1, 99))


def mail():
    return random.choice(["scout", "agent", "talent", "recruit", "fcdeals"]) + str(random.randint(1, 99)) + \
        "@" + random.choice(["gmail.com", "yahoo.com", "outlook.com"])


def fill(t):
    return (t.replace("{amt}", random.choice(AMOUNTS)).replace("{club}", random.choice(CLUBS))
            .replace("{city}", random.choice(CITIES)).replace("{country}", random.choice(COUNTRIES))
            .replace("{when}", random.choice(WHENS)).replace("{name}", random.choice(NAMES))
            .replace("{app}", random.choice(APPS)).replace("{num}", phone())
            .replace("{acct}", acct()).replace("{handle}", handle()).replace("{mail}", mail()))


# ---------------------------------------------------------------- clause banks
# (text, [labels], lang)   lang: en / sw / sh (Sheng / code-mixed)
C = []
def add(labels, lang, *texts):
    for t in texts:
        C.append((t, labels, lang))

# advance fee
add([A], "en",
    "You need to pay {amt} registration fee before we can proceed.",
    "Send {amt} for the medical test so we can book your trial.",
    "A processing fee of {amt} is required to release your offer letter.",
    "Kindly deposit {amt} for your kit and club jersey.",
    "To confirm your slot, a refundable deposit of {amt} is needed.",
    "Pay {amt} for trial coordination and we will send the invitation.",
    "The scouting fee is {amt}, it must be paid first.",
    "Send the agent commitment fee of {amt}, then we continue.",
    "We need {amt} for your work permit processing.",
    "Clear the documentation fee of {amt} before the contract is sent.",
    "There is a small fee of {amt} for trial insurance.",
    "Please pay {amt} for the club administrative charges today.")
add([A], "sw",
    "Unahitaji kulipa {amt} ya usajili kabla hatujaendelea.",
    "Tuma {amt} kwa ajili ya vipimo vya afya ili tukupangie majaribio.",
    "Lipa {amt} ya ada ya kuchakata barua yako ya ofa.",
    "Tafadhali weka amana ya {amt} kuthibitisha nafasi yako.",
    "Ada ya wakala ni {amt}, lazima ilipwe kwanza.",
    "Tunahitaji {amt} kwa ajili ya jezi na vifaa vyako.")
add([A], "sh",
    "Tuma {amt} ya registration ndio tuendelee.",
    "Weka {amt} ya medical ndio nikubook trials yako.",
    "Bro ni {amt} tu ya processing, ukituma tunasonga.",
    "Tuma {amt} ya kit, jersey yako iko tayari.",
    "Hizo {amt} za slot zinahitajika kwanza ndio ofa itoke.",
    "Lipa {amt} ya agent fee halafu tuko sawa.")

# personal account payment
add([P], "en",
    "Send it to my personal M-Pesa {num}.",
    "Pay to this number {num}, it is my private line.",
    "Use my personal account, the club account is under audit.",
    "Deposit to my Equity account {acct}, not the club's.",
    "Send via M-Pesa to {num} (name: {name}).",
    "Pay through my cousin's number {num}, it is faster.",
    "The club cashier is away, so send it to my own number {num}.",
    "Use Western Union in my name, {name}.",
    "Pay to my Airtel Money {num}.")
add([P], "sw",
    "Tuma kwa namba yangu ya M-Pesa {num}.",
    "Lipa kwa akaunti yangu binafsi {acct}.",
    "Tuma kwa namba hii {num}, ni yangu mwenyewe.",
    "Akaunti ya klabu ina shida, tumia namba yangu {num}.")
add([P], "sh",
    "Tuma kwa mpesa yangu {num}.",
    "Piga mpesa kwa hii namba {num}, ni yangu.",
    "Weka kwa account yangu binafsi, ya club iko na issue.",
    "Tuma kwa jamaa wangu {num} ndio iwe fiti.")

# urgency
add([U], "en",
    "Do it today, the slot closes tonight.",
    "Only 2 slots left, decide now.",
    "If you delay, we will give your place to another player.",
    "You have 24 hours or the offer expires.",
    "This is your last chance, other players are waiting.",
    "The deadline is in one hour, hurry.",
    "Reply immediately or you lose this opportunity.",
    "The club is closing registration tomorrow morning.")
add([U], "sw",
    "Fanya haraka, nafasi inaisha leo usiku.",
    "Zimebaki nafasi mbili tu, amua sasa.",
    "Ukichelewa tutampa mchezaji mwingine.",
    "Una saa 24 tu au ofa itaisha.",
    "Hii ni nafasi yako ya mwisho.")
add([U], "sh",
    "Fanya haraka bro, slot inaisha leo.",
    "Zimebaki slots mbili tu, amua sasa.",
    "Ukilala tutampa mwingine.",
    "Una masaa 24 tu ama ofa inaexpire.",
    "Sasa hivi ama uwache.")

# secrecy
add([S], "en",
    "Don't tell anyone about this, not even your coach.",
    "Keep this between us.",
    "Do not inform your parents or your club yet.",
    "Please don't tell other players or your agent about this deal.",
    "This is confidential, no one should know until you sign.",
    "Do not mention this to KEFWA or anyone else.",
    "Delete this chat after reading.",
    "If your club finds out, the deal is cancelled.")
add([S], "sw",
    "Usimwambie mtu yeyote, hata kocha wako.",
    "Hii iwe siri kati yetu.",
    "Usiwaambie wazazi wako wala klabu yako bado.",
    "Ni siri, hakuna anayepaswa kujua mpaka usaini.",
    "Futa ujumbe huu baada ya kusoma.")
add([S], "sh",
    "Usimwambie mtu, hata coach wako.",
    "Iwe siri yetu bro.",
    "Usiseme na parents ama club yako bado.",
    "Ni confidential, hakuna anafaa kujua till usaini.",
    "Delete hii chat ukimaliza kusoma.")

# guaranteed outcome
add([G], "en",
    "You will sign for sure, the club has already agreed.",
    "Your selection is 100% guaranteed.",
    "I guarantee you a contract after the trial.",
    "The deal is already done, you only need to confirm.",
    "You cannot fail, the coach has already chosen you.",
    "Your place in the squad is secured.",
    "We will place you in a top European club, no doubt.",
    "Success is guaranteed, we have done this for many players.")
add([G], "sw",
    "Utasaini kwa hakika, klabu imekubali tayari.",
    "Kuchaguliwa kwako ni uhakika wa asilimia 100.",
    "Nakuhakikishia mkataba baada ya majaribio.",
    "Mkataba tayari umekamilika, unahitaji tu kuthibitisha.",
    "Huwezi kukosa, kocha ameshakuchagua.")
add([G], "sh",
    "Utasign bila shaka, club imeshakubali.",
    "Ni 100% umepita bro.",
    "Nakuhakikishia contract baada ya trials.",
    "Deal imeisha, wewe thibitisha tu.",
    "Huwezi fail, coach ameshakuchagua.")

# visa / travel arranged
add([V], "en",
    "Your visa is already approved.",
    "We have arranged your flight to {country}.",
    "The club has booked your air ticket and hotel in {city}.",
    "Your visa is being processed by our embassy contact.",
    "Your travel documents for {country} are ready.",
    "Your work permit for {country} is ready, we only need your passport copy.",
    "Your flight to {country} is booked for next week.")
add([V], "sw",
    "Visa yako tayari imeidhinishwa.",
    "Tumeshapanga safari yako ya ndege kwenda {country}.",
    "Klabu imeshakata tiketi na kuweka hoteli {city}.",
    "Kibali chako cha kufanya kazi {country} kiko tayari.")
add([V], "sh",
    "Visa yako imepita bro.",
    "Tushakupangia flight kwenda {country}.",
    "Club imebook ticket na hotel {city}.",
    "Work permit yako ya {country} iko ready.")

# off platform
add([O], "en",
    "Let's move this chat to Telegram, WhatsApp is not safe for contracts.",
    "Add me on {app}, my handle is {handle}.",
    "Email me privately at {mail} instead of here.",
    "Let us continue on Signal, it is more secure.",
    "Message me on my other number {num} not here.",
    "Contact me on Instagram DM for the next steps.",
    "Join our private group on Telegram {handle} for the next steps.")
add([O], "sw",
    "Tuhamie Telegram, WhatsApp si salama kwa mikataba.",
    "Niongeze kwenye Telegram {handle}.",
    "Nitumie barua pepe binafsi {mail} badala ya hapa.",
    "Tuendelee kwa Signal, ni salama zaidi.")
add([O], "sh",
    "Tuhamie Telegram bro, hii WhatsApp si safe.",
    "Niadd kwa Telegram {handle}.",
    "Niandikie kwa DM Insta tuendelee.",
    "Tupigie kwa namba nyingine {num}, si hapa.")

# multi-label clauses (scams rarely come one signal at a time)
add([A, P], "en",
    "Send {amt} to my personal M-Pesa {num} to secure your slot.",
    "Pay the {amt} fee to my own number {num}.",
    "Deposit {amt} to my account {acct}, then we proceed.")
add([A, P], "sw", "Tuma {amt} kwa namba yangu {num} kuthibitisha nafasi yako.",
    "Lipa {amt} ya usajili kwa akaunti yangu binafsi {acct}.")
add([A, P], "sh", "Tuma {amt} kwa mpesa yangu {num} ndio slot ikae.",
    "Weka {amt} ya registration kwa namba yangu {num}.")
add([A, U], "en", "Send {amt} today or you lose the slot.",
    "Pay {amt} within 24 hours, the offer expires after that.")
add([A, U], "sw", "Tuma {amt} leo au nafasi itaenda.", "Lipa {amt} ndani ya saa 24 la sivyo ofa itaisha.")
add([A, U], "sh", "Tuma {amt} leo ama slot iende.", "Tuma {amt} sasa hivi bro, slot ni chache.")
add([A, G], "en", "Pay {amt} and your selection is guaranteed.", "Send {amt} and the contract is yours for sure.")
add([A, G], "sw", "Lipa {amt} na uchaguzi wako ni uhakika.", "Tuma {amt} na mkataba ni wako kwa hakika.")
add([A, G], "sh", "Tuma {amt} na contract ni yako bila shaka.", "Weka {amt} halafu umepita 100%.")
add([A, V], "en", "Your visa is approved, just send {amt} for the ticket.",
    "Your flight is booked, we only need {amt} for the visa fee.")
add([A, V], "sw", "Visa yako imeidhinishwa, tuma tu {amt} ya tiketi.")
add([A, V], "sh", "Visa imepita bro, tuma tu {amt} ya ticket.")
add([A, P, S], "en", "Send {amt} to {num} and don't tell anyone.")
add([A, P, S], "sh", "Tuma {amt} kwa {num} na usimwambie mtu.")
add([A, P, U], "en", "Send {amt} to my personal number {num} before tonight.")
add([A, P, U], "sh", "Tuma {amt} kwa namba yangu {num} kabla ya usiku.")
add([G, S], "en", "You will sign for sure, the club has already agreed. Keep it between us.",
    "The deal is done, but do not tell anyone until you sign.")
add([G, S], "sw", "Utasaini kwa hakika, ila usimwambie mtu yeyote.")
add([G, S], "sh", "Utasign bila shaka, ila iwe siri yetu.")
add([O, S], "en", "Let's talk on Telegram and keep this private.", "Move to Signal, and do not tell anyone about this deal.")
add([O, S], "sw", "Tuhamie Telegram na hii iwe siri yetu.")
add([O, S], "sh", "Tuhamie Telegram bro, iwe siri yetu.")
add([V, G], "en", "Your visa and ticket are ready, you will definitely play in {country}.")
add([V, G], "sh", "Visa na ticket ziko ready, utacheza {country} bila shaka.")
add([U, G], "en", "Decide now, your place is guaranteed only today.")
add([U, G], "sh", "Amua sasa, nafasi yako ni uhakika leo tu.")
add([P, O], "en", "Message me on {app} and send it to my own number {num}.")
add([P, O], "sh", "Niandikie Telegram halafu tuma kwa namba yangu {num}.")

# v3 additions: unfamiliar fee wordings, deadlines, relatives' numbers
add([A], "en",
    "Cover the courier charges for your documents.",
    "Send the clearance fee so the contract can be released.",
    "You must pay the bank charges to transfer your signing bonus.",
    "The club needs the medical money before we proceed.",
    "Pay for the DHL delivery of your contract.",
    "A small facilitation fee is needed for your papers.",
    "A customs fee of {amt} must be cleared before your documents arrive.")
add([A], "sw", "Lipa gharama za courier za nyaraka zako.", "Tuma pesa ya medical kwa ajili ya nyaraka.",
    "Lipa ada ya kuachilia mkataba wako.")
add([A], "sh", "Lipa courier charges za documents zako.", "Tuma pesa ya clearance ndio contract itoke.",
    "Kuna facilitation fee ndogo ya {amt}.")
add([P], "en", "Send to my wife's number {num}.", "Use my brother's number {num}, it is the same as mine.",
    "Send it to my assistant's M-Pesa {num}.")
add([P], "sw", "Tuma kwa namba ya mke wangu {num}.", "Tumia namba ya kaka yangu {num}.")
add([P], "sh", "Tuma kwa number ya brother yangu {num}.", "Tuma kwa namba ya mke wangu {num}, ni same.")
add([A, U], "en", "The club needs the medical money before Friday.", "Send the courier fee by tomorrow morning.")
add([A, U], "sw", "Tuma pesa ya medical kabla ya Ijumaa.")
add([A, U], "sh", "Tuma pesa ya courier kabla kesho asubuhi.")
add([U], "en", "You must decide by tomorrow.", "I need your answer before Friday.", "Decide by tomorrow, the club will not wait.")
add([U], "sw", "Lazima uamue kesho.", "Nipe jibu kabla ya Ijumaa.")
add([U], "sh", "Lazima uamue kesho bro.", "Nipe answer kabla Friday.")
add([G], "en", "Your contract is waiting for you.", "The contract is already prepared in your name.")
add([G], "sw", "Mkataba wako unakusubiri.")
add([G], "sh", "Contract yako inakusubiri.")

# ---------------------------------------------------------------- v4 additions
# Fee wordings WITHOUT an amount and with unusual nouns (courier, customs, clearance, "medical money",
# bank charges, stamp duty...). v3 taught "fee" mostly through "Send {amt}" templates, so these were missed.
add([A], "en",
    "The courier company wants payment before they dispatch your papers.",
    "Settle the shipping cost of your contract first.",
    "Pay the customs clearance so your documents can be released.",
    "We need you to cover the bank transfer charges for your signing bonus.",
    "The doctor must be paid before your medical, send the money first.",
    "Please send the fee for your visa interview.",
    "You must pay for your own insurance before we continue.",
    "Send the processing charges and I will push your file.",
    "Your file is stuck until the admin fee is paid.",
    "Just send the fee today and we proceed.",
    "Top up the transport and accommodation money for the trial.",
    "The embassy appointment has to be paid for by you first.",
    "Pay a commitment of {amt} so we can hold your place.",
    "Kindly clear the pending charges on your file.",
    "Pay the stamp duty on your contract.",
    "Send money for the DHL so the letter can reach you.",
    "Your documents are at the airport, pay the release charges to get them.",
    "The agent's share must be paid before the club contacts you.",
    "Remember the legal fees for drafting your contract, those are on you.",
    "I will need the money for the medical examination first.",
    "Transfer the amount for the translation of your documents.",
    "Pay for your trial jersey and boots upfront.",
    "Before the contract is emailed, the courier fee must be settled.",
    "Cover the paperwork costs and the offer is yours.",
    "You will pay the signing tax in advance, it is refunded later.")
add([A], "sw",
    "Lipa gharama za usafirishaji wa nyaraka zako kwanza.",
    "Tuma pesa ya kuchakata faili yako.",
    "Ada lazima ilipwe kabla mkataba haujatumwa.",
    "Gharama za benki za kutuma bonasi yako ni juu yako.",
    "Tuma ada ya mahojiano ya ubalozi.",
    "Lipa {amt} ya ushuru wa forodha ili nyaraka zitoke.",
    "Malipo ya courier lazima yafanywe kabla barua haijatumwa.",
    "Daktari anahitaji kulipwa kabla ya vipimo, tuma pesa kwanza.",
    "Faili yako imekwama mpaka ulipe ada ya utawala.",
    "Tuma pesa ya bima yako kabla ya kuendelea.",
    "Lipa gharama za kutafsiri nyaraka zako.")
add([A], "sh",
    "Cover courier ya docs zako bro.",
    "Tuma ile pesa ya medical ndio daktari akuone.",
    "Lipa customs ndio documents ziachiliwe.",
    "Faile yako imekwama mpaka ulipe admin fee.",
    "Tuma fee ya processing leo.",
    "Bank charges za kutuma signing bonus ni zako.",
    "Weka {amt} ya DHL ndio letter ikufikie.",
    "Lipa ile clearance ndio contract itumwe.",
    "Pesa ya embassy appointment ni yako, tuma kwanza.",
    "Nitumie ile pesa ya docs ndio nipush file yako.",
    "Kuna charges kidogo za kulipa kabla ya contract.")

# fee + time pressure, no amount
add([A, U], "en",
    "The courier fee must reach me before Monday.",
    "Pay the clearance charges by tonight or the documents go back.",
    "Send the medical money before the weekend, the doctor is booked.")
add([A, U], "sw", "Lipa gharama za courier kabla ya kesho.", "Tuma pesa ya vipimo kabla ya wikendi.")
add([A, U], "sh", "Tuma pesa ya clearance kabla usiku.", "Lipa courier fee kabla Monday bro.")

# fee + personal account, relatives
add([A, P], "en",
    "Send the courier money to my sister's number {num}.",
    "Pay the medical fee to my friend's M-Pesa {num}, he handles it for me.")
add([A, P], "sh", "Tuma pesa ya courier kwa number ya dada yangu {num}.")
add([A, P, S], "en", "Pay the customs fee to {num} and keep it between us.")
add([A, P, S], "sw", "Lipa ada ya forodha kwa {num} na iwe siri yetu.")
add([P], "en", "Send it to my sister's number {num}.", "Use my friend's M-Pesa {num}, it is easier.",
    "The club cashier's line is {num}, send there.")
add([P], "sw", "Tuma kwa namba ya dada yangu {num}.")
add([P], "sh", "Tuma kwa number ya rafiki yangu {num}.")

# selection / contract announced as already certain, with and without a fee
add([G], "en",
    "Congratulations! You have been selected for {club}.",
    "Congratulations, you are our new signing.",
    "Welcome to the team, your contract is ready for signing.",
    "Great news, the club wants you and the paperwork is waiting.",
    "You made it! Only the paperwork remains.",
    "The offer letter is already signed on our side.",
    "Congratulations, your contract has been approved.",
    "You are in. The club has decided to take you.")
add([G], "sw",
    "Hongera! Umechaguliwa kujiunga na {club}.",
    "Karibu kwenye timu, mkataba wako uko tayari kusainiwa.",
    "Habari njema, klabu inakutaka na karatasi zinakusubiri.",
    "Umepita! Kilichobaki ni karatasi tu.")
add([G], "sh",
    "Hongera bro! Umepickiwa na {club}.",
    "Karibu kwa team, contract yako iko ready kusignwa.",
    "Umepita! Imebaki paperwork tu.",
    "Club imekubali kukuchukua, wewe subiri tu.")
add([A, G], "en",
    "Congratulations! Your offer is signed on our side, just pay the courier fee to receive it.",
    "Great news, you are selected. Cover the clearance charges and the contract is yours.",
    "You have been picked. Only the processing fee stands between you and the contract.")
add([A, G], "sw", "Hongera, umechaguliwa! Lipa ada ya courier upokee mkataba wako.")
add([A, G], "sh", "Hongera bro, umepita! Lipa courier ndio contract ikufikie.")
add([A, G, U], "en", "You are selected, just send the fee today and the contract is yours.")
add([A, V], "en", "Your flight is booked and your visa is approved, just settle the clearance charges.",
    "The ticket is arranged, only the airport release fee remains.")

# neutral context (safe to mix into scam messages; label-free)
NEUTRAL = [
    ("Hello, I am an agent working with {club}.", "en"),
    ("Hi, we saw your video and liked your skills.", "en"),
    ("Good morning, how are you?", "en"),
    ("Thank you for your interest.", "en"),
    ("We have a great opportunity for you.", "en"),
    ("You are a very talented player.", "en"),
    ("Hujambo, mimi ni wakala wa {club}.", "sw"),
    ("Tuliona video yako na tumevutiwa.", "sw"),
    ("Habari ya asubuhi, uko aje?", "sw"),
    ("Karibu sana, tuna nafasi nzuri kwako.", "sw"),
    ("Niaje bro, mimi ni scout wa {club}.", "sh"),
    ("Tulipata video yako, uko na talent sana.", "sh"),
    ("Poa bro, nina chance fiti kwako.", "sh"),
    # v5: amounts that are NOT payment requests, so "amount" stops being a shortcut for "fee"
    ("The club offers a monthly salary of {amt} once a contract is signed.", "en"),
    ("Last season the club paid its players between {amt} and {amt} a month.", "en"),
    ("Your match bonus would be {amt} per win.", "en"),
    ("He was sold to a club in {country} for {amt}.", "en"),
    ("Mshahara wa mwezi ni {amt} baada ya kusaini mkataba.", "sw"),
    ("Bonasi ya kila ushindi ni {amt}.", "sw"),
    ("Salary ni {amt} kwa mwezi ukisign contract.", "sh"),
    ("Jamaa alinunuliwa na club ya {country} kwa {amt}.", "sh"),
]

# hard negatives: legit messages, some mention money/visa/trials but are NOT scam signals
HARD_NEG = [
    ("Your trial is on {when} at the {club} ground. Bring boots and ID. There is no fee.", "en"),
    ("Thanks for sending your video. Our scout will review it and reply by email.", "en"),
    ("The club will cover your accommodation and meals during the trial week.", "en"),
    ("Please confirm the details with your current club before we continue.", "en"),
    ("We will send the official invitation letter from the club email.", "en"),
    ("We never ask players for money. If anyone does, report to KEFWA.", "en"),
    ("You are responsible for your own passport; the club will send an invitation letter.", "en"),
    ("Registration is handled by the club directly with the federation, you pay nothing.", "en"),
    ("Training starts at 8am, please arrive 30 minutes early.", "en"),
    ("Selection depends on your performance on the day, nothing is promised.", "en"),
    ("You are welcome to bring a parent or guardian.", "en"),
    ("Kindly verify our licence number on the FKF website.", "en"),
    ("Don't share your passwords or PIN with anyone.", "en"),
    ("You may apply for your own visa at the embassy once a contract is signed.", "en"),
    ("The trial results will be shared by email next week.", "en"),
    ("Please bring your birth certificate and a recent medical report from any clinic.", "en"),
    ("Feel free to speak to your coach or club before you decide.", "en"),
    ("Let me know if you have questions about the trial schedule.", "en"),
    ("Kumbuka kuleta viatu na kitambulisho kesho asubuhi.", "sw"),
    ("Hakuna malipo yoyote kwa majaribio.", "sw"),
    ("Tutakutumia barua rasmi kupitia barua pepe ya klabu.", "sw"),
    ("Asante kwa video yako, tutakujulisha matokeo.", "sw"),
    ("Mazoezi ni Jumamosi saa mbili asubuhi, leta viatu na kitambulisho.", "sw"),
    ("Klabu haitakuomba pesa yoyote, ukiombwa ripoti KEFWA.", "sw"),
    ("Hakuna ahadi, uchaguzi unategemea uchezaji wako siku hiyo.", "sw"),
    ("Unaweza kuongea na kocha wako kabla ya kuamua.", "sw"),
    ("Trials ni Saturday morning, leta boots na ID, hakuna pesa inatakiwa.", "sh"),
    ("Poa, tutakuambia matokeo baada ya trials.", "sh"),
    ("Club haiulizi pesa, ukiulizwa report KEFWA.", "sh"),
    ("Hakuna promise bro, ni performance yako siku ya trials.", "sh"),
    ("Tutakutumia invitation letter rasmi kupitia email ya club.", "sh"),
    ("Travel and visa arrangements are your responsibility until a contract is signed; the club will then send an invitation letter.", "en"),
    ("If you are selected, the club will explain the visa process to you and your family.", "en"),
    ("Visa requirements differ by country, please check the embassy website.", "en"),
    ("Flights to the trial are not provided, please plan your own transport.", "en"),
    ("We cannot guarantee selection, only the coach decides.", "en"),
    ("There is no rush, take your time to decide.", "en"),
    ("Take a few days to talk with your family before you decide.", "en"),
    ("No deadline, reply when you are ready.", "en"),
    ("The deadline for submitting your documents is 30 April, as listed on the club website.", "en"),
    ("Mahitaji ya visa hutofautiana kila nchi, angalia tovuti ya ubalozi.", "sw"),
    ("Hatuwezi kuhakikisha kuchaguliwa, kocha ndiye anaamua.", "sw"),
    ("Hakuna haraka, chukua muda wako kuamua.", "sw"),
    ("Requirements za visa ni tofauti kila country, angalia website ya embassy.", "sh"),
    ("Hatuwezi guarantee selection, coach ndiye anaamua.", "sh"),
    ("Hakuna haraka bro, chukua time uamue.", "sh"),
    ("No one can guarantee selection, but you are welcome at the open trial, it is free.", "en"),
    ("We don't promise any contract, the trial is open and free of charge.", "en"),
    ("Nothing is guaranteed, the coach decides on the day.", "en"),
    ("Be careful of anyone who guarantees you a contract, real clubs never do.", "en"),
    ("The club will pay for your courier and documents, you pay nothing.", "en"),
    ("Medical checks are done by the club doctor at no cost to you.", "en"),
    ("We will give you our answer by Friday, no pressure on your side.", "en"),
    ("Hakuna anayeweza kuhakikisha mkataba, ila karibu kwenye majaribio bila malipo.", "sw"),
    ("Jihadhari na mtu anayekuhakikishia mkataba, klabu za kweli hazifanyi hivyo.", "sw"),
    ("Vipimo vya afya vinafanywa na daktari wa klabu bila gharama kwako.", "sw"),
    ("Hakuna mtu anaweza guarantee contract, ila karibu trials ni free.", "sh"),
    ("Jihadhari na mtu anayekuguarantee contract, club ya kweli haifanyi hivyo.", "sh"),
    ("Medical inafanywa na daktari wa club bila malipo.", "sh"),
    ("Karibu na parent ama guardian wako kwa trials.", "sh"),
]

# ---- v4 hard negatives 
HARD_NEG += [
    # negated / disclaimed visa and travel (should NOT flag visa_travel_arranged)
    ("We cannot promise a visa, that depends on the embassy.", "en"),
    ("No one can guarantee a visa, the embassy decides.", "en"),
    ("A visa is not guaranteed, you apply once you have a signed contract.", "en"),
    ("We can't guarantee the visa, but you are welcome at the open trial.", "en"),
    ("Visa approval is decided by the embassy, not by us or the club.", "en"),
    ("Hakuna anayeweza kuhakikisha visa, ni ubalozi unaoamua.", "sw"),
    ("Hatuwezi kuahidi visa, ubalozi ndio huamua.", "sw"),
    ("Hatuwezi guarantee visa, embassy ndio inaamua.", "sh"),
    ("Hakuna mtu anaweza promise visa bro, ni embassy.", "sh"),
    # who pays for courier / documents / medical (should NOT flag a fee)
    ("The club handles courier and document costs, you do not pay anything.", "en"),
    ("The club doctor does the medical examination at no cost to the player.", "en"),
    ("Any shipping of documents is paid by the club directly.", "en"),
    ("Hakuna malipo ya courier, klabu inalipa gharama zote za nyaraka.", "sw"),
    ("Club ndio inalipa courier na documents, wewe hulipi kitu.", "sh"),
    # congratulations after a real trial is not a guarantee
    ("Congratulations on a good trial. The coach will review all players and decide next week.", "en"),
    ("Thanks for attending the trial. We will email the results to everyone next week.", "en"),
    ("Hongera kwa majaribio mazuri. Kocha atapitia wachezaji wote kisha atuamue wiki ijayo.", "sw"),
    ("Poa kwa trials bro, coach ataangalia wote halafu tutakuambia matokeo.", "sh"),
    # v5: amounts in legitimate messages (club pays, salary, bonuses, past transfers)
    ("The club pays your {amt} registration with the federation, you pay nothing.", "en"),
    ("Your accommodation worth {amt} a week is paid by the club.", "en"),
    ("The club will give you a {amt} signing bonus after you sign your contract.", "en"),
    ("The trial is free. Players in the first team earn about {amt} a month.", "en"),
    ("Please do not send any money, not even {amt}, to anyone claiming to be from the club.", "en"),
    ("Last year a player left for {country} on a {amt} transfer, the fee was paid by the buying club.", "en"),
    ("The club reimburses {amt} for transport once you arrive at the trial, keep your receipt.", "en"),
    ("The kit worth {amt} is provided by the club on your first day.", "en"),
    ("Klabu inalipa {amt} ya usajili wako, wewe hulipi chochote.", "sw"),
    ("Malazi ya {amt} kwa wiki yanalipwa na klabu.", "sw"),
    ("Mshahara wa wachezaji wa timu ya kwanza ni karibu {amt} kwa mwezi, majaribio ni bure.", "sw"),
    ("Usitume pesa, hata {amt}, kwa mtu yeyote anayejifanya ni wa klabu.", "sw"),
    ("Club inalipa {amt} ya registration yako, wewe hulipi kitu.", "sh"),
    ("Jersey ya {amt} inapeanwa na club siku ya kwanza.", "sh"),
    ("Usitume pesa bro, hata {amt}, kwa mtu anayejifanya ni wa club.", "sh"),
    ("Salary ya first team ni karibu {amt} kwa mwezi, trials ni free.", "sh"),
    # deadlines that are normal
    ("Please send your signed registration form back by Friday, there is no charge.", "en"),
    ("Tafadhali tuma fomu yako kabla ya Ijumaa, hakuna malipo.", "sw"),
]

# ---------------------------------------------------------------- noise
SMS = [(r"\bplease\b", "pls"), (r"\byou\b", "u"), (r"\byour\b", "ur"), (r"\btomorrow\b", "tmrw"),
       (r"\bbecause\b", "cz"), (r"\bmessage\b", "msg"), (r"\bmoney\b", "mny"), (r"\bbefore\b", "b4")]
EMOJI = ["🙏", "⚽", "🔥", "✅", "🙂", "💪", "🇰🇪"]


def noisy(t):
    r = random.random()
    if r < 0.12:
        t = t.lower()
    if random.random() < 0.15:
        t = re.sub(r"[.,!]", "", t)
    if random.random() < 0.15:
        for pat, rep in SMS:
            if random.random() < 0.6:
                t = re.sub(pat, rep, t, flags=re.I)
    if random.random() < 0.08 and len(t) > 12:
        i = random.randrange(1, len(t) - 2)
        t = t[:i] + t[i + 1] + t[i] + t[i + 2:]
    if random.random() < 0.06:
        t = t.upper()
    if random.random() < 0.10:
        t += " " + random.choice(EMOJI)
    return t


# ---------------------------------------------------------------- split by clause (hold-out)
def bucket(text, mod=7):
    return int(hashlib.md5(text.encode()).hexdigest(), 16) % mod == 0  # 1/mod -> val only


def pools(split):
    want_val = split == "val"
    pos = [c for c in C if bucket(c[0]) == want_val]
    neu = [n for i, n in enumerate(NEUTRAL) if (i % 4 == 0) == want_val] or NEUTRAL
    neg = [h for i, h in enumerate(HARD_NEG) if (i % 4 == 0) == want_val] or HARD_NEG
    return pos, neu, neg


def pick_lang():
    return random.choices(["en", "sw", "sh"], weights=[5, 2.5, 3])[0]


def make_message(pos, neu, neg):
    mixed = random.random() < 0.2
    lang = pick_lang()

    def choose(pool, lg):
        cand = [x for x in pool if x[-1] == lg] or pool
        # fee clauses are now ~1/3 of all scam clauses; down-weight them so fees stay near 40% of rows
        w = [FEE_WEIGHT if (len(x) == 3 and A in x[1]) else 1.0 for x in cand]
        return random.choices(cand, weights=w)[0]

    r = random.random()
    if r < 0.28:  # benign / hard negative
        k = random.choices([1, 2, 3], weights=[4, 4, 2])[0]
        parts, seen_neg = [], set()
        for _ in range(k):
            for _try in range(10):
                lg = pick_lang() if mixed else lang
                cl = choose(neg, lg)
                if cl[0] not in seen_neg:
                    break
            seen_neg.add(cl[0])
            parts.append(fill(cl[0]))
        return " ".join(parts), []
    # scam-like
    k = random.choices([1, 2, 3], weights=[4, 4, 2])[0]
    labels, parts, used = set(), [], set()
    if random.random() < 0.5:
        parts.append(fill(choose(neu, lang)[0]))
    for _ in range(k):
        for _try in range(10):
            lg = pick_lang() if mixed else lang
            cl = choose(pos, lg)
            if cl[0] not in used:
                break
        used.add(cl[0])
        parts.append(fill(cl[0]))
        labels |= set(cl[1])
    return " ".join(parts), sorted(labels)


def build(n, split):
    pos, neu, neg = pools(split)
    rows, seen = [], set()
    while len(rows) < n:
        text, labs = make_message(pos, neu, neg)
        text = noisy(text)
        if text in seen:
            continue
        seen.add(text)
        rows.append({"text": text, **{l: int(l in labs) for l in LABELS}})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    random.seed(SEED)
    tr, va = build(N_TRAIN, "train"), build(N_VAL, "val")
    tr.to_csv("synthetic_train.csv", index=False)
    va.to_csv("synthetic_val.csv", index=False)
    print("clauses:", len(C), "| train", len(tr), "| val", len(va))
    print("label counts (train):\n", tr[LABELS].sum().to_string())
    print("no-label share (train): %.2f" % (tr[LABELS].sum(axis=1) == 0).mean())
