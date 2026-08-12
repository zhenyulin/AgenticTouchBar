// Rendered widths, in points, of a list of strings -- measured through the
// same Cocoa text layout BetterTouchTool draws its Touch Bar widgets with, so
// the lyrics widget can tell how much of the row the Now Playing widget
// beside it is taking. See viewport.py, its only caller.
//
// JavaScript rather than Python because neither interpreter on this machine
// ships PyObjC (/usr/bin/python3 is the Command Line Tools build, which
// leaves it out), while osascript's ObjC bridge is always present.
//
// Input is one JSON argument rather than one argument per string: a title
// starting with a dash would otherwise read as an osascript flag.
//
//   osascript -l JavaScript text_width.js '{"fontSize":12,"strings":["a"]}'
//
// Output is one width per line, in the order the strings came in.
ObjC.import('Cocoa');

function run(argv) {
  var request = JSON.parse(argv[0]);
  var font = $.NSFont.systemFontOfSize(request.fontSize);
  var attributes = $.NSDictionary.dictionaryWithObjectForKey(
    font,
    $.NSFontAttributeName
  );

  var widths = [];
  for (var index = 0; index < request.strings.length; index++) {
    var string = $(request.strings[index]);
    widths.push(string.sizeWithAttributes(attributes).width.toFixed(2));
  }
  return widths.join('\n');
}
