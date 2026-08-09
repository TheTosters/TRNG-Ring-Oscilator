python3 usb_read.py -s aBCDEFr -o channelA.bin
python3 usb_read.py -s AbCDEFr -o channelB.bin
python3 usb_read.py -s ABcDEFr -o channelC.bin
python3 usb_read.py -s ABCdEFr -o channelD.bin
python3 usb_read.py -s ABCDeFr -o channelE.bin
python3 usb_read.py -s ABCDEfr -o channelF.bin
python3 usb_read.py -s abcdefr -o channelA-F.bin

python3 unpack_single_channel.py channelA.bin outputA.bin
python3 unpack_single_channel.py channelB.bin outputB.bin
python3 unpack_single_channel.py channelC.bin outputC.bin
python3 unpack_single_channel.py channelD.bin outputD.bin
python3 unpack_single_channel.py channelE.bin outputE.bin
python3 unpack_single_channel.py channelF.bin outputF.bin
python3 unpack_single_channel.py channelA-F.bin outputA-F.bin --bits 6

echo "Analyze channel A"
ea_non_iid -v -i outputA.bin 1 > results.txt

echo "Analyze channel B"
ea_non_iid -v -i outputB.bin 1 >> results.txt

echo "Analyze channel C"
ea_non_iid -v -i outputC.bin 1 >> results.txt

echo "Analyze channel D"
ea_non_iid -v -i outputD.bin 1 >> results.txt

echo "Analyze channel E"
ea_non_iid -v -i outputE.bin 1 >> results.txt

echo "Analyze channel F"
ea_non_iid -v -i outputF.bin 1 >> results.txt

echo "Analyze channel A-F"
ea_non_iid -v -i outputA-F.bin 6 >> results.txt
